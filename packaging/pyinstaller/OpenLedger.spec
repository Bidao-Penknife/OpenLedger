# -*- mode: python ; coding: utf-8 -*-
"""Build the first Windows x64 directory bundle from the installed src-layout project."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files


project_root = Path(SPECPATH).resolve().parents[1]
package_data = collect_data_files("openledger.resources")
package_data += collect_data_files("tzdata")
package_data += [
    (str(project_root / "LICENSE"), "."),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
]

analysis = Analysis(
    [str(project_root / "main.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=package_data,
    hiddenimports=["tzdata"],
    hookspath=[str(project_root / "packaging" / "pyinstaller" / "hooks")],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "PyQt5",
        "PyQt6",
        "PySide2",
        "tkinter",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtVirtualKeyboard",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickControls2",
        "PySide6.QtQuickWidgets",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineQuick",
        "PySide6.QtWebEngineWidgets",
    ],
    noarchive=False,
    optimize=0,
)

# Fail closed if a new dependency/hook bypasses the optional-plugin exclusion.
# Do not remove DLLs from the final TOC: that could leave dependent binaries broken.
for destination, source, _kind in analysis.binaries:
    filename = Path(destination).name.casefold()
    if filename.startswith(("qt6pdf", "qt6virtualkeyboard", "qt6qml", "qt6quick")) or (
        Path(destination).stem.casefold().startswith(("qpdf", "qvirtualkeyboard", "qtvirtualkeyboard"))
    ):
        raise RuntimeError(
            f"Unexpected optional Qt module in the Widgets bundle: {destination} ({source})"
        )

archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="OpenLedger",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    version=str(project_root / "build" / "release" / "windows-version.txt"),
    disable_windowed_traceback=False,
    contents_directory="_internal",
)
bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="OpenLedger",
)
