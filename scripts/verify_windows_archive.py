"""Check the actual portable ZIP and start its extracted application natively."""

import argparse
import hashlib
import json
import stat
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def main() -> int:
    """Keep extraction isolated and retain startup evidence after removing the bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args()
    archive_path = arguments.archive.resolve(strict=True)
    output = arguments.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Portable ZIP CRC failed.")
        names = [item.filename.replace("\\", "/") for item in archive.infolist()]
        if len(set(name.casefold() for name in names)) != len(names):
            raise RuntimeError("Duplicate portable ZIP paths.")
        for item, name in zip(archive.infolist(), names, strict=True):
            relative = PurePosixPath(name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or ":" in name
                or relative.parts[0] != "OpenLedger"
                or stat.S_ISLNK(item.external_attr >> 16)
            ):
                raise RuntimeError("Unsafe portable ZIP path.")
        for required in [
            "OpenLedger/OpenLedger.exe",
            "OpenLedger/LICENSE",
            "OpenLedger/THIRD_PARTY_NOTICES.md",
            "OpenLedger/licenses/manifest.json",
            "OpenLedger/licenses/Qt/LGPL-3.0-only.txt",
            "OpenLedger/_internal/openledger/resources/themes/light.qss",
            "OpenLedger/_internal/openledger/resources/migrations/0001.sql",
            "OpenLedger/_internal/openledger/resources/migrations/0002.sql",
            "OpenLedger/_internal/openledger/resources/currencies.json",
            "OpenLedger/_internal/openledger/resources/help/manual.html",
            "OpenLedger/_internal/openledger/resources/translations/openledger_en_US.qm",
        ]:
            if required not in names:
                raise RuntimeError(f"Required file missing: {required}")
        sql = archive.read("OpenLedger/_internal/openledger/resources/migrations/0001.sql")
        expected_sql = (project / "src/openledger/resources/migrations/0001.sql").read_bytes()
        if sql != expected_sql:
            raise RuntimeError("Bundled migration bytes differ from source.")
        for resource in ("migrations/0002.sql", "currencies.json", "help/manual.html"):
            if (
                archive.read("OpenLedger/_internal/openledger/resources/" + resource)
                != (project / "src/openledger/resources" / resource).read_bytes()
            ):
                raise RuntimeError("Bundled currency schema/manual differs from source.")
        translation = archive.read(
            "OpenLedger/_internal/openledger/resources/translations/openledger_en_US.qm"
        )
        expected_translation = (
            project / "src/openledger/resources/translations/openledger_en_US.qm"
        ).read_bytes()
        if translation != expected_translation:
            raise RuntimeError("Bundled translation differs from the reviewed catalog.")
        forbidden = [
            "qt6pdf",
            "qt6qml",
            "qt6quick",
            "qt6virtualkeyboard",
            "qtvirtualkeyboardplugin",
        ]
        if any(any(part in name.casefold() for part in forbidden) for name in names):
            raise RuntimeError("Unexpected optional Qt modules.")
        if any(name.casefold().endswith("/icuuc.dll") for name in names):
            raise RuntimeError("A shadow ICU library entered the bundle.")
        with tempfile.TemporaryDirectory(prefix="解压验证 space-", dir=output) as directory:
            destination = Path(directory).resolve()
            if not destination.is_relative_to(output):
                raise RuntimeError("Extraction must stay within the validation directory.")
            archive.extractall(destination)
            result = subprocess.run(
                [
                    sys.executable,
                    str(project / "scripts/smoke_app.py"),
                    "--executable",
                    str(destination / "OpenLedger/OpenLedger.exe"),
                    "--output-dir",
                    str(output / "extracted"),
                ],
                cwd=destination,
                capture_output=True,
                timeout=45,
                check=False,
            )
            if result.returncode:
                raise RuntimeError(f"Extracted startup failed: {result.stderr!r}")
    payload = {
        "status": "passed",
        "archive": str(archive_path),
        "sha256": digest,
        "archive_bytes": archive_path.stat().st_size,
        "zip_entries": len(names),
        "crc_passed": True,
        "migration_sha256": hashlib.sha256(sql).hexdigest(),
        "translation_sha256": hashlib.sha256(translation).hexdigest(),
        "required_resources_present": True,
        "unexpected_qt_modules_absent": True,
        "extracted_native_startup_passed": True,
    }
    (output / "archive-check.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
