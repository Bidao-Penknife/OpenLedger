"""Load reviewed Qt translation catalogs before creating any application widgets."""

from importlib.resources import as_file, files

from PySide6.QtCore import QCoreApplication, QTranslator


def install_language(app: QCoreApplication, language: str) -> QTranslator | None:
    """Install the requested catalog; language changes take effect on next startup."""
    if language == "zh_CN":
        return None
    if language != "en_US":
        raise ValueError("Unsupported interface language")
    translator = QTranslator(app)
    resource = files("openledger.resources").joinpath("translations", "openledger_en_US.qm")
    with as_file(resource) as path:
        if not translator.load(str(path)):
            raise ValueError("Missing or invalid translation catalog")
    if not app.installTranslator(translator):
        raise ValueError("Unable to install translation catalog")
    return translator
