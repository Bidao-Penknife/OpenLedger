"""Load read-only bundled resources in source, wheels and frozen builds."""

from importlib.resources import files
from pathlib import PurePosixPath


def read_text_resource(relative_path: str) -> str:
    """Read UTF-8 package data while rejecting absolute or escaping resource names."""
    path = PurePosixPath(relative_path)
    if not relative_path or path.is_absolute() or ".." in path.parts:
        raise ValueError("Resource name must remain inside the package.")
    if "\\" in relative_path or ":" in relative_path:
        raise ValueError("Resource name must use a relative POSIX path.")
    return files("openledger.resources").joinpath(*path.parts).read_text(encoding="utf-8")
