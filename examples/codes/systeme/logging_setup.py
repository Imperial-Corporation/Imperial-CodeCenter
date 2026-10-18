"""Journalisation : console + fichier tournant, avec format horodaté."""

import logging
import logging.handlers
import tempfile
from pathlib import Path


def setup_logger(name: str, path: Path) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if logger.handlers:  # évite les doublons si appelé plusieurs fois
        return logger

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s : %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    rotating = logging.handlers.RotatingFileHandler(
        path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    rotating.setFormatter(formatter)
    logger.addHandler(console)
    logger.addHandler(rotating)
    return logger


if __name__ == "__main__":
    log_file = Path(tempfile.gettempdir()) / "exemple.log"
    log = setup_logger("demo", log_file)
    log.info("démarrage")
    log.warning("espace disque bas : %d%%", 92)
