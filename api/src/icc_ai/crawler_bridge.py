# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Accès au paquet `icc_crawler`, livré à côté de l'API (dossier `crawler/`).

Le crawler est un paquet autonome (il se lance aussi en ligne de commande).
L'API réutilise ses règles de langage et de classement sans copier le code :
ce module ajoute `<racine>/crawler/src` au chemin d'import si besoin, ce
qui fonctionne à l'identique dans le dépôt et dans le dossier installé.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
CRAWLER_SRC = ROOT / "crawler" / "src"


def ensure_importable() -> bool:
    """Rend `icc_crawler` importable ; retourne False s'il est introuvable."""
    try:
        import icc_crawler  # noqa: F401
        return True
    except ImportError:
        pass
    if CRAWLER_SRC.is_dir() and str(CRAWLER_SRC) not in sys.path:
        sys.path.insert(0, str(CRAWLER_SRC))
    try:
        import icc_crawler  # noqa: F401,F811
        return True
    except ImportError:
        return False


def crawler_pythonpath() -> str:
    """Valeur de PYTHONPATH à donner au sous-processus du crawler."""
    return str(CRAWLER_SRC) if CRAWLER_SRC.is_dir() else ""
