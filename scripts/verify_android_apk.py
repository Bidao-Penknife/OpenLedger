"""Verify the actual Android preview APK, using official SDK inspection tools."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
MIGRATION_SHA256 = "a369dc6c350105a8f2d00772132fb57cc31b38c8703def7409d2bf50cfea9347"


def inspect(apk: Path, sdk_root: Path) -> dict[str, Any]:
    """Reject corrupt, unexpected, unsigned or excessively privileged previews."""
    build_tools = sdk_root / "build-tools" / "35.0.0"

    def run(tool: str, *arguments: str) -> str:
        result = subprocess.run(
            [str(build_tools / tool), *arguments],
            check=True,
            capture_output=True,
            encoding="utf-8",
            timeout=90,
        )
        return result.stdout

    with zipfile.ZipFile(apk) as archive:
        if archive.testzip() is not None:
            raise ValueError("APK CRC failed")
        names = archive.namelist()
        required_notices = {
            "CPython.txt",
            "Chaquopy.txt",
            "Kotlin.txt",
            "OpenLedger.txt",
            "OpenSSL.txt",
            "SQLite.txt",
            "libffi.txt",
            "bzip2.txt",
            "zlib.txt",
            "tzdata.txt",
            "xz.txt",
            "xz-LGPL-2.1.txt",
            "NOTICE.md",
            "sources.json",
        }
        if not all(f"assets/licenses/{name}" in names for name in required_notices):
            raise ValueError("Required runtime license materials missing")
        if len(names) != len(set(names)):
            raise ValueError("Duplicate APK entry")
        abis = sorted({name.split("/")[1] for name in names if name.startswith("lib/")})
        if abis != ["arm64-v8a", "x86_64"]:
            raise ValueError("Unexpected Android architectures")
        python_payload = archive.read("assets/chaquopy/app.imy")
        with zipfile.ZipFile(io.BytesIO(python_payload)) as python_archive:
            migration = python_archive.read("openledger/resources/migrations/0001.sql")
            if hashlib.sha256(migration).hexdigest() != MIGRATION_SHA256:
                raise ValueError("Android migration differs from the shared immutable schema")
            if any(
                "presentation/" in name or "credentials" in name
                for name in python_archive.namelist()
            ):
                raise ValueError("Desktop-only code entered the Android source closure")
    badging = run("aapt2.exe", "dump", "badging", str(apk))
    if (
        "name='org.openledger.android.preview'" not in badging
        or "versionName='0.1.0-alpha1-preview'" not in badging
    ):
        raise ValueError("Unexpected APK identity")
    if "minSdkVersion:'24'" not in badging or "targetSdkVersion:'36'" not in badging:
        raise ValueError("Unexpected Android API range")
    permissions = run("aapt2.exe", "dump", "permissions", str(apk))
    if re.search(r"uses-permission", permissions):
        raise ValueError("Offline preview must not request application permissions")
    manifest = run("aapt2.exe", "dump", "xmltree", str(apk), "--file", "AndroidManifest.xml")
    if not re.search(r"android:allowBackup\([^)]*\)=false\b", manifest):
        raise ValueError("Android cloud backup must be explicitly disabled")
    signature = run("apksigner.bat", "verify", "--verbose", "--print-certs", str(apk))
    run("zipalign.exe", "-c", "-P", "16", "4", str(apk))
    certificate = re.search(r"certificate SHA-256 digest: ([0-9a-f]+)", signature)
    if certificate is None:
        raise ValueError("Missing verified APK signing certificate")
    with apk.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "status": "passed",
        "apk": str(apk.resolve()),
        "bytes": apk.stat().st_size,
        "sha256": digest,
        "package": "org.openledger.android.preview",
        "version": "0.1.0-alpha1-preview",
        "min_sdk": 24,
        "target_sdk": 36,
        "abis": abis,
        "runtime_license_files": sorted(required_notices),
        "migration_sha256": MIGRATION_SHA256,
        "requested_permissions": [],
        "android_cloud_backup": False,
        "signature_verified": True,
        "zip_alignment_16kb_verified": True,
        "signer_certificate_sha256": certificate.group(1),
        "distribution": "debug_preview",
        "physical_device_tested": False,
    }


def main() -> int:
    """Publish inspectable APK evidence and return failure on any rejected check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, required=True)
    parser.add_argument("--sdk-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect(args.apk.resolve(), args.sdk_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": result["status"], "sha256": result["sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
