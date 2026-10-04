"""Validate release identities, record bundle bytes, and collect reviewable assets."""

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from packaging.version import Version

from openledger import __version__

APP_ID = "2EA4E61C-9182-4B6C-BFF8-995365131679"
COMPILER_VERSION = "6.7.3"
COMPILER_SHA256 = "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732"
COMPILER_URL = "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe"


def checked_version(value: str, tag: str | None = None) -> Version:
    """Accept a canonical bounded release identity and require an exact tag match."""
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.dev\d+)?", value):
        raise ValueError("Version must be canonical major.minor.patch with an optional qualifier")
    parsed = Version(value)
    if str(parsed) != value or any(number > 65535 for number in parsed.release):
        raise ValueError("Version is noncanonical or exceeds Windows version fields")
    if tag is not None and tag != "v" + value:
        raise ValueError("Tag and source version differ")
    windows_version(value)
    return parsed


def windows_version(value: str) -> tuple[int, int, int, int]:
    """Order dev, alpha, beta, rc, and final versions within the four PE fields."""
    parsed = Version(value)
    if len(parsed.release) != 3 or any(number > 65535 for number in parsed.release):
        raise ValueError("Three bounded release fields are required")
    serial = 65535
    if parsed.dev is not None:
        if parsed.dev >= 10000:
            raise ValueError("Development serial is too large")
        serial = parsed.dev
    elif parsed.pre is not None:
        kind, number = parsed.pre
        if number >= 10000:
            raise ValueError("Prerelease serial is too large")
        serial = {"a": 10000, "b": 20000, "rc": 30000}[kind] + number
    return (*parsed.release, serial)


def sha256(path: Path) -> str:
    """Stream a file digest without loading complete release assets into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_source_name(name: str) -> str:
    """Reject paths and record formats that do not belong in public source archives."""
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or ".." in path.parts
        or any(ord(char) < 32 for char in name)
        or any(part in {".git", ".venv", "build", "dist", "data", "backups"} for part in path.parts)
        or path.suffix.lower()
        in {".db", ".sqlite", ".sqlite3", ".olbackup", ".key", ".pem", ".log"}
        or (path.name.startswith(".env") and path.name != ".env.example")
        or path.name in {"settings.json", "ai-settings.json", "update-settings.json"}
    ):
        raise ValueError(f"Unsafe source path: {name!r}")
    return name


def source_archive(root: Path, target: Path) -> int:
    """Archive every current source file including authorized, unstaged additions."""
    root = root.resolve()
    raw = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"], cwd=root
    )
    names = sorted(set(raw.decode("utf-8").strip("\0").split("\0")))
    if not names or len({name.casefold() for name in names}) != len(names):
        raise ValueError("Empty source list or Windows path collision")
    for name in names:
        safe_source_name(name)
        path = root / name
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError(f"Missing source or source escapes checkout: {name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            archive.write(root / name, "OpenLedger/" + name)
    with zipfile.ZipFile(target) as archive:
        if archive.testzip() is not None:
            raise ValueError("Source archive CRC failed")
        for name in names:
            if archive.read("OpenLedger/" + name) != (root / name).read_bytes():
                raise ValueError("Source changed during archive creation")
    return len(names)


def prepare(root: Path, version: str) -> dict[str, Any]:
    """Write the numeric PE version and installation identity before freezing."""
    checked_version(version)
    directory = root / "build/release"
    directory.mkdir(parents=True, exist_ok=True)
    numeric = windows_version(version)
    pe = (
        "VSVersionInfo(ffi=FixedFileInfo(filevers="
        + repr(numeric)
        + ", prodvers="
        + repr(numeric)
        + ", mask=0x3f, flags=0, OS=0x40004, "
        "fileType=1, subtype=0, date=(0,0)), kids=[StringFileInfo([StringTable('040904B0', ["
        "StringStruct('CompanyName','OpenLedger contributors'),"
        "StringStruct('FileDescription','OpenLedger personal finance'),"
        "StringStruct('FileVersion'," + repr(version) + "),"
        "StringStruct('ProductVersion'," + repr(version) + "),"
        "StringStruct('ProductName','OpenLedger'),"
        "StringStruct('OriginalFilename','OpenLedger.exe')])]),"
        "VarFileInfo([VarStruct('Translation',[1033,1200])])])\n"
    )
    (directory / "windows-version.txt").write_text(pe, encoding="utf-8")
    result = {"version": version, "windows_version": ".".join(map(str, numeric)), "app_id": APP_ID}
    (directory / "identity.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def bundle_manifest(root: Path, version: str) -> dict[str, Any]:
    """Record all distributable files and an installer marker without private data."""
    identity = prepare(root, version)
    bundle = root / "dist/windows/OpenLedger"
    (bundle / ".openledger-install.ini").write_text(
        "[OpenLedger]\nAppId="
        + APP_ID
        + "\nVersion="
        + version
        + "\nWindowsVersion="
        + identity["windows_version"]
        + "\n",
        encoding="utf-8",
    )
    entries = []
    for path in sorted(bundle.rglob("*")):
        if not path.is_file() or path.name == "bundle-manifest.json":
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(bundle.resolve()):
            raise ValueError("Bundle contains an external link")
        name = path.relative_to(bundle).as_posix()
        if path.suffix.lower() in {".sqlite3", ".olbackup", ".key", ".pem", ".log"}:
            raise ValueError("Private material entered the bundle")
        entries.append({"path": name, "bytes": path.stat().st_size, "sha256": sha256(path)})
    result = {**identity, "files": entries}
    (bundle / "bundle-manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def collect_assets(root: Path, output: Path, version: str, tag: str | None) -> dict[str, Any]:
    """Collect exactly three reviewed assets and checksums for a release candidate."""
    import shutil

    parsed = checked_version(version, tag)
    output.mkdir(parents=True, exist_ok=True)
    source = output / f"OpenLedger-{version}-source.zip"
    count = source_archive(root, source)
    names = [source]
    for directory, name in (
        ("dist/windows", f"OpenLedger-{version}-windows-x64.zip"),
        ("dist/installer", f"OpenLedger-{version}-windows-x64-setup.exe"),
    ):
        original = root / directory / name
        if not original.is_file():
            raise ValueError("Required asset missing: " + str(original))
        target = output / name
        if original.resolve() != target.resolve():
            shutil.copyfile(original, target)
        names.append(target)
    assets = [
        {"file": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)} for path in names
    ]
    checksums = "".join(f"{item['sha256']}  {item['file']}\n" for item in assets)
    (output / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
    for item in assets:
        (output / (str(item["file"]) + ".sha256")).write_text(
            f"{item['sha256']}  {item['file']}\n", encoding="utf-8"
        )
    result = {
        "version": version,
        "tag": "v" + version,
        "prerelease": parsed.is_prerelease,
        "source_files": count,
        "assets": assets,
    }
    (output / "release-manifest.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result


def main() -> int:
    """Run a local preparation or collection step; never publish a release."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "bundle", "collect", "check-tag"])
    parser.add_argument("--version", default=__version__)
    parser.add_argument("--tag")
    parser.add_argument("--output-dir", type=Path, default=Path("dist/release"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.action == "prepare":
        result = prepare(root, args.version)
    elif args.action == "bundle":
        result = bundle_manifest(root, args.version)
        result = {key: value for key, value in result.items() if key != "files"}
    elif args.action == "collect":
        result = collect_assets(root, args.output_dir.resolve(), args.version, args.tag)
    else:
        checked_version(args.version, args.tag)
        if args.tag is None:
            parser.error("check-tag requires --tag")
        result = {"status": "passed", "version": args.version, "tag": args.tag}
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
