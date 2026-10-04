"""Maintain reviewed Qt catalogs, including statically resolvable dynamic labels.

Run without flags to merge source changes, ``--check`` to verify completeness,
or ``--compile`` to compile a complete catalog with the installed Qt lrelease.
New messages are deliberately unfinished until a contributor reviews them.
"""

from __future__ import annotations

import argparse
import ast
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from string import Formatter

PROJECT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT / "src" / "openledger" / "presentation"
CATALOG = PROJECT / "src" / "openledger" / "resources" / "translations" / "openledger_en_US.ts"


@dataclass(frozen=True, slots=True)
class Message:
    """A source-context pair and every call site for Qt translator lookup."""

    context: str
    source: str
    locations: tuple[tuple[str, int], ...]


def _values(node: ast.AST, bindings: dict[str, ast.AST]) -> tuple[ast.AST, ...]:
    if isinstance(node, ast.Name) and node.id in bindings:
        return _values(bindings[node.id], bindings)
    if isinstance(node, (ast.Tuple, ast.List)):
        return tuple(node.elts)
    return ()


def _dynamic_sources(
    argument: ast.Name, ancestors: tuple[ast.AST, ...], bindings: dict[str, ast.AST]
) -> tuple[str, ...]:
    for ancestor in reversed(ancestors):
        generators: tuple[ast.For | ast.comprehension, ...] = ()
        if isinstance(ancestor, ast.For):
            generators = (ancestor,)
        elif isinstance(ancestor, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            generators = tuple(ancestor.generators)
        for generator in generators:
            target = generator.target
            position: int | None = None
            if isinstance(target, ast.Name) and target.id == argument.id:
                position = -1
            elif isinstance(target, (ast.Tuple, ast.List)):
                for index, member in enumerate(target.elts):
                    if isinstance(member, ast.Name) and member.id == argument.id:
                        position = index
            if position is None:
                continue
            sources: list[str] = []
            for item in _values(generator.iter, bindings):
                if position >= 0:
                    members = _values(item, bindings)
                    if position >= len(members):
                        continue
                    item = members[position]
                if isinstance(item, ast.Constant) and isinstance(item.value, str):
                    sources.append(item.value)
            if sources:
                return tuple(sources)
    raise ValueError(f"Unresolved translation source: {argument.id}")


def _translation_context(
    receiver: ast.AST, ancestors: tuple[ast.AST, ...], context: str | None
) -> str:
    if isinstance(receiver, ast.Name):
        if receiver.id == "self" and context is not None:
            return context
        for ancestor in reversed(ancestors):
            if isinstance(ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for argument in (*ancestor.args.posonlyargs, *ancestor.args.args):
                    if argument.arg == receiver.id and isinstance(argument.annotation, ast.Name):
                        return argument.annotation.id
    raise ValueError(f"Unresolved translation context: {ast.unparse(receiver)}")


def collect_messages(root: Path = SOURCE_ROOT) -> tuple[Message, ...]:
    """Extract literal tr sources and labels in explicit tuples, without executing code."""
    grouped: dict[tuple[str, str], set[tuple[str, int]]] = defaultdict(set)
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        bindings: dict[str, ast.AST] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        bindings[target.id] = node.value

        def visit(
            node: ast.AST,
            ancestors: tuple[ast.AST, ...],
            context: str | None,
            *,
            source_path: Path = path,
            source_bindings: dict[str, ast.AST] = bindings,
        ) -> None:
            if isinstance(node, ast.ClassDef):
                context = node.name
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "tr"
                and node.args
            ):
                translation_context = _translation_context(node.func.value, ancestors, context)
                argument = node.args[0]
                sources: tuple[str, ...]
                if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                    sources = (argument.value,)
                elif isinstance(argument, ast.Name):
                    sources = _dynamic_sources(argument, ancestors, source_bindings)
                else:
                    raise ValueError(f"Dynamic translation at {source_path}:{node.lineno}")
                try:
                    filename = source_path.relative_to(SOURCE_ROOT.parent).as_posix()
                    filename = "../../" + filename
                except ValueError:
                    filename = source_path.name
                for source in sources:
                    grouped[(translation_context, source)].add((filename, node.lineno))
            for child in ast.iter_child_nodes(node):
                visit(child, (*ancestors, node), context)

        visit(tree, (), None)
    return tuple(
        Message(context, source, tuple(sorted(locations)))
        for (context, source), locations in sorted(grouped.items())
    )


def placeholders(text: str) -> Counter[str]:
    """Compare Qt and Python format placeholders without changing braces or positions."""
    tokens = Counter(re.findall(r"%L?[1-9][0-9]*|%n", text))
    for _, name, specifier, conversion in Formatter().parse(text):
        if name is not None:
            tokens[
                "{"
                + name
                + ("!" + conversion if conversion else "")
                + (":" + specifier if specifier else "")
                + "}"
            ] += 1
    return tokens


def read_catalog(path: Path) -> dict[tuple[str, str], str]:
    """Read reviewed translations and reject ambiguous duplicate context/source entries."""
    if not path.exists():
        return {}
    root = ET.parse(path).getroot()
    result: dict[tuple[str, str], str] = {}
    for context in root.findall("context"):
        name = context.findtext("name", "")
        for message in context.findall("message"):
            key = (name, message.findtext("source", ""))
            if key in result:
                raise ValueError(f"Duplicate translation: {key}")
            result[key] = message.findtext("translation", "")
    return result


def check_catalog(path: Path = CATALOG, root: Path = SOURCE_ROOT) -> None:
    """Require every extracted label to have a reviewed, placeholder-preserving translation."""
    translated = read_catalog(path)
    expected = {(message.context, message.source) for message in collect_messages(root)}
    missing = expected - translated.keys()
    empty = {key for key, value in translated.items() if not value.strip()}
    mismatched = {
        key for key, value in translated.items() if placeholders(key[1]) != placeholders(value)
    }
    unfinished = [
        node
        for node in ET.parse(path).getroot().iter("translation")
        if node.attrib.get("type") in {"unfinished", "obsolete", "vanished"}
    ]
    if missing or empty or mismatched or unfinished:
        raise ValueError(
            f"Catalog incomplete: missing={len(missing)}, empty={len(empty)}, "
            f"placeholders={len(mismatched)}, unfinished={len(unfinished)}"
        )


def merge_catalog(path: Path = CATALOG, root: Path = SOURCE_ROOT) -> int:
    """Preserve reviewed translations and mark new sources for human translation."""
    previous = read_catalog(path)
    document = ET.Element("TS", {"version": "2.1", "language": "en_US", "sourcelanguage": "zh_CN"})
    contexts: dict[str, ET.Element] = {}
    messages = collect_messages(root)
    for item in messages:
        context = contexts.get(item.context)
        if context is None:
            context = ET.SubElement(document, "context")
            ET.SubElement(context, "name").text = item.context
            contexts[item.context] = context
        message = ET.SubElement(context, "message")
        for filename, line in item.locations:
            ET.SubElement(message, "location", {"filename": filename, "line": str(line)})
        ET.SubElement(message, "source").text = item.source
        value = previous.get((item.context, item.source), "")
        translation = ET.SubElement(message, "translation", {} if value else {"type": "unfinished"})
        translation.text = value
    ET.indent(document, "    ")
    payload = (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n'
        + ET.tostring(document, encoding="unicode")
        + "\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8", newline="\n")
    return len(messages)


def compile_catalog(path: Path = CATALOG) -> None:
    """Compile only a complete reviewed catalog using the project's Qt tooling."""
    check_catalog(path)
    executable = Path(sys.executable).parent / (
        "pyside6-lrelease.exe" if sys.platform == "win32" else "pyside6-lrelease"
    )
    command = str(executable) if executable.is_file() else shutil.which("pyside6-lrelease")
    if command is None:
        raise ValueError("Qt lrelease is not installed")
    subprocess.run([command, str(path), "-qm", str(path.with_suffix(".qm"))], check=True)


def main() -> int:
    """Merge sources, verify a reviewed catalog, or compile it for package resources."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify without editing")
    parser.add_argument("--compile", action="store_true", help="Compile a complete catalog")
    args = parser.parse_args()
    if args.check or args.compile:
        check_catalog()
    else:
        print(f"Merged {merge_catalog()} messages; review new unfinished translations.")
    if args.compile:
        compile_catalog()
    if args.check:
        print("Translation coverage and placeholders verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
