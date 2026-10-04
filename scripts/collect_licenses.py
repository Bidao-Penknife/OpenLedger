"""Copy actual installed dependency licenses and version metadata into a bundle."""

import argparse
import json
import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path
from typing import Any

DEPENDENCIES = (
    "PySide6",
    "PySide6-Essentials",
    "PySide6-Addons",
    "shiboken6",
    "tzdata",
    "openpyxl",
    "et-xmlfile",
    "defusedxml",
    "packaging",
    "pyinstaller",
    "pyinstaller-hooks-contrib",
)


def collect(output: Path) -> dict[str, Any]:
    """Require license material for the interpreter and each selected dependency."""
    output.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for name in DEPENDENCIES:
        package = distribution(name)
        target = output / name
        target.mkdir(parents=True, exist_ok=True)
        selected = [
            entry
            for entry in package.files or ()
            if any(
                word in entry.name.lower()
                for word in ("license", "licence", "copying", "copyright", "notice")
            )
        ]
        copied: list[str] = []
        for entry in selected:
            source = Path(str(package.locate_file(entry)))
            if not source.is_file():
                continue
            relative = Path(str(entry))
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"Unexpected license entry in {name}: {entry}")
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            copied.append(destination.relative_to(output).as_posix())
        if not copied:
            raise RuntimeError(f"No installed license texts found for {name}.")
        metadata_text = package.read_text("METADATA")
        if metadata_text is None:
            raise RuntimeError(f"Installed metadata is missing for {name}.")
        (target / "METADATA.txt").write_text(metadata_text, encoding="utf-8")
        records.append(
            {
                "name": name,
                "version": package.version,
                "license": package.metadata.get("License-Expression")
                or package.metadata.get("License"),
                "files": copied,
            }
        )

    interpreter_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not interpreter_license.is_file():
        interpreter_license = Path(sys.base_prefix) / "LICENSE"
    if not interpreter_license.is_file():
        raise RuntimeError("The interpreter's license file is required for a frozen bundle.")
    python_target = output / "Python"
    python_target.mkdir(exist_ok=True)
    shutil.copyfile(interpreter_license, python_target / "LICENSE.txt")

    root = Path(__file__).resolve().parent.parent
    vendor = root / "packaging" / "licenses"
    for required in ("LGPL-3.0-only.txt", "GPL-3.0-only.txt", "sources.md"):
        if not (vendor / "Qt" / required).is_file():
            raise RuntimeError(f"Missing supplemental Qt license material: {required}")
    shutil.copytree(vendor, output, dirs_exist_ok=True)
    result = {"python": sys.version, "dependencies": records}
    (output / "manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    """Collect notices for the exact environment selected by the build lock."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    result = collect(arguments.output_dir.resolve())
    print(
        json.dumps(
            {
                "dependencies": len(result["dependencies"]),
                "output": str(arguments.output_dir.resolve()),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
