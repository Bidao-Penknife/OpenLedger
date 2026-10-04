"""Resource packaging must not depend on an application's launch directory."""

from pathlib import Path

import pytest

from openledger.infrastructure.resources import read_text_resource


def test_style_is_readable_outside_source_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert "Q" in read_text_resource("themes/light.qss")


@pytest.mark.parametrize("name", ["", "../README.md", "/absolute.qss", "C:/file", "themes\\file"])
def test_resource_cannot_escape_package(name: str) -> None:
    with pytest.raises(ValueError):
        read_text_resource(name)
