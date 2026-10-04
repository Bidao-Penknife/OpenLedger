"""Small, bounded startup logs with no finance or credential payloads."""

import logging
from logging.handlers import RotatingFileHandler

from openledger.infrastructure.platform.paths import AppPaths


def configure_logging(paths: AppPaths) -> logging.Logger:
    """Configure only the application logger, with UTF-8 and bounded rotation."""
    logger = logging.getLogger("openledger")
    for handler in tuple(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = RotatingFileHandler(
        paths.logs / "startup.log", maxBytes=512 * 1024, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
