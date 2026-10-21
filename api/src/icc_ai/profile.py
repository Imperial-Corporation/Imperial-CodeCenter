# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Profils de ressources : faire tourner CodeCenter sur une petite machine.

`CODECENTER_PROFILE` choisit un jeu de réglages :

* `standard` : comportement historique (aucun réglage imposé) ;
* `lite`     : petit serveur / PC modeste (2 threads, peu de connexions, pauses d'indexation) ;
* `minimal`  : très petite machine ou disque dur lent (1 thread, pauses plus longues) ;
* `auto`     : choisit d'après la mémoire, les cœurs et le type de disque (défaut : `standard`).

Les réglages du profil sont des **valeurs par défaut** : une variable d'environnement
déjà définie par l'utilisateur n'est jamais écrasée. Sans `CODECENTER_PROFILE`, rien ne
change par rapport à la 1.0.3.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROFILES: dict[str, dict[str, str]] = {
    "standard": {},
    "lite": {
        "CODECENTER_DB_MAX_CONNECTIONS": "4",
        "CODECENTER_ENGINE_THREADS": "2",
        "CODECENTER_INDEX_PAUSE_MS": "20",
        "CODECENTER_SEARCH_SCAN_MB": "32",
        "CODECENTER_RESCAN_SECS": "3600",
        "CODECENTER_DEPOT_SECS": "60",
        "CODECENTER_SEARCH_CONCURRENCY": "2",
        "CODECENTER_NICE": "10",
    },
    "minimal": {
        "CODECENTER_DB_MAX_CONNECTIONS": "2",
        "CODECENTER_ENGINE_THREADS": "1",
        "CODECENTER_INDEX_PAUSE_MS": "60",
        "CODECENTER_SEARCH_SCAN_MB": "16",
        "CODECENTER_RESCAN_SECS": "7200",
        "CODECENTER_DEPOT_SECS": "120",
        "CODECENTER_SEARCH_CONCURRENCY": "1",
        "CODECENTER_NICE": "15",
    },
}

AUTO_LITE_MEM_MB = 4096
AUTO_MINIMAL_MEM_MB = 1536


def memory_mb() -> int | None:
    """Mémoire totale (Mio), ou None si elle ne peut pas être lue (non Linux)."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def is_rotational(path: Path) -> bool | None:
    """True si le dossier est sur un disque à plateaux (HDD). None si inconnu."""
    try:
        device = os.stat(path).st_dev
        major, minor = os.major(device), os.minor(device)
        link = Path(f"/sys/dev/block/{major}:{minor}").resolve()
        for candidate in (link, link.parent):
            flag = candidate / "queue" / "rotational"
            if flag.is_file():
                return flag.read_text().strip() == "1"
    except (OSError, ValueError):
        pass
    return None


def detect(data_dir: Path | None = None) -> str:
    """Profil conseillé pour cette machine."""
    mem = memory_mb()
    cpus = os.cpu_count() or 1
    rotational = is_rotational(data_dir) if data_dir and Path(data_dir).exists() else None
    if (mem is not None and mem < AUTO_MINIMAL_MEM_MB) or (cpus <= 1 and rotational):
        return "minimal"
    if (mem is not None and mem < AUTO_LITE_MEM_MB) or cpus <= 2 or rotational:
        return "lite"
    return "standard"


def resolve_name(data_dir: Path | None = None) -> str:
    raw = os.environ.get("CODECENTER_PROFILE", "").strip().lower()
    if raw in PROFILES:
        return raw
    if raw == "auto":
        return detect(data_dir)
    return "standard"  # absent ou inconnu : comportement historique


def apply(data_dir: Path | None = None) -> dict:
    """Applique le profil (valeurs par défaut seulement) ; renvoie ce qui a été retenu."""
    name = resolve_name(data_dir)
    applied: dict[str, str] = {}
    for key, value in PROFILES[name].items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    lower_priority()
    return {"profile": name, "applied": applied}


def lower_priority() -> None:
    """Baisse la priorité CPU (`CODECENTER_NICE`) et disque (`CODECENTER_IONICE`) du processus.

    Les threads créés ensuite (moteur Rust, indexation) en héritent : l'indexation ne gêne plus
    le reste de la machine. Sans effet hors Linux ou sans droit suffisant.
    """
    try:
        niceness = int(os.environ.get("CODECENTER_NICE", "0") or 0)
    except ValueError:
        niceness = 0
    if niceness > 0:
        try:
            os.nice(min(niceness, 19))
        except (OSError, AttributeError):
            pass

    ionice = os.environ.get("CODECENTER_IONICE", "").strip().lower()
    if ionice and sys.platform.startswith("linux"):
        _set_ioprio({"idle": (3, 0), "low": (2, 7), "normal": (2, 4)}.get(ionice))


def _set_ioprio(setting: tuple[int, int] | None) -> None:
    if not setting:
        return
    try:
        import ctypes

        syscall = ctypes.CDLL(None, use_errno=True).syscall
        number = {"x86_64": 251, "aarch64": 30, "arm64": 30}.get(os.uname().machine)
        if number is None:
            return
        cls, level = setting
        syscall(number, 1, 0, (cls << 13) | level)  # IOPRIO_WHO_PROCESS, ce processus
    except Exception:  # best effort
        pass


def search_concurrency() -> int:
    try:
        return max(1, min(16, int(os.environ.get("CODECENTER_SEARCH_CONCURRENCY", "4"))))
    except ValueError:
        return 4
