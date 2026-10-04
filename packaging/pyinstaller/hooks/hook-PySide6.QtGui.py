"""Keep the standard QtGui collection while excluding two unused optional plugins.

Reviewed against PyInstaller 6.22.3. Its Qt collector validates plugin dependencies
but returns the plugins themselves; Analysis scans their recursive DLL dependencies
later. Filtering this list before Analysis prevents PDF/virtual-keyboard plugins
from bringing their optional Qt modules into this QWidget application.
"""

from pathlib import Path

from PyInstaller.utils.hooks.qt import add_qt6_dependencies

hiddenimports, binaries, datas = add_qt6_dependencies(__file__)

# This desktop uses the Windows system IME and has no PDF image decoder or Qt
# Virtual Keyboard UI. Keep all other default plugins, including qwindows,
# common image formats, style plugins, and their actual library dependencies.
_excluded_plugin_prefixes = ("qpdf", "qvirtualkeyboard", "qtvirtualkeyboard")
binaries = [
    entry
    for entry in binaries
    if not Path(entry[0]).stem.casefold().startswith(_excluded_plugin_prefixes)
]
