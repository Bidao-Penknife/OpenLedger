"""Run a fixed, SHA256-verified Kotlin formatter without global installation."""

import argparse
import hashlib
import os
import subprocess
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL_SHA256 = "b8fbb814808d8da33f74a7bbacb6d1748cef81c0202a7f829b87139520b51273"
TOOL_URL = (
    "https://github.com/Kotlin/ktfmt/releases/download/v0.64/ktfmt-0.64-with-dependencies.jar"
)


def main() -> int:
    """Format Kotlin sources, or reject differences when CI requests --check."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    options = parser.parse_args()
    java_root = os.environ.get("JAVA_HOME")
    if not java_root:
        parser.error("JAVA_HOME must point to JDK 17")
    java = Path(java_root) / "bin" / ("java.exe" if os.name == "nt" else "java")
    tool = ROOT / "build" / "tools" / "ktfmt-0.64.jar"
    if not tool.is_file():
        tool.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(TOOL_URL, headers={"User-Agent": "OpenLedger-build"})
        with urllib.request.urlopen(request, timeout=90) as response:
            content = response.read(71_428_315)
        if len(content) != 71_428_314:
            raise ValueError("Unexpected Kotlin formatter artifact size")
        if hashlib.sha256(content).hexdigest() != TOOL_SHA256:
            raise ValueError("Kotlin formatter checksum mismatch")
        tool.write_bytes(content)
    with tool.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != TOOL_SHA256:
            raise ValueError("Kotlin formatter checksum mismatch")
    files = sorted((ROOT / "android" / "app" / "src").rglob("*.kt"))
    files += sorted((ROOT / "android").glob("*.gradle.kts"))
    files += sorted((ROOT / "android" / "app").glob("*.gradle.kts"))
    arguments = [str(java), "-jar", str(tool), "--kotlinlang-style"]
    if options.check:
        arguments += ["--dry-run", "--set-exit-if-changed"]
    return subprocess.run(
        arguments + [str(path) for path in files], check=False, timeout=120
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
