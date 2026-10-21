# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Moniteur de ressources (sans dépendance) et métriques Prometheus."""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

_STARTED = time.time()


def _proc_status() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in {"VmRSS", "VmHWM", "Threads"}:
                values[key] = int(rest.split()[0])
    except (OSError, ValueError, IndexError):
        pass
    return values


def _meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(rest.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return values


def _disk(path: Path) -> dict | None:
    try:
        usage = shutil.disk_usage(path if path.exists() else path.parent)
        return {"total": usage.total, "free": usage.free, "used": usage.used}
    except OSError:
        return None


def snapshot(data_dir: Path, codes_dir: Path | None, profile: dict | None = None) -> dict:
    """Instantané des ressources : processus, machine, disques."""
    status = _proc_status()
    mem = _meminfo()
    try:
        load = os.getloadavg()
    except (OSError, AttributeError):
        load = None
    accounts_db = Path(data_dir) / "accounts.db"
    return {
        "uptime_s": int(time.time() - _STARTED),
        "process": {
            "rss": status.get("VmRSS", 0) * 1024, "rss_peak": status.get("VmHWM", 0) * 1024,
            "threads": status.get("Threads", threading.active_count()), "pid": os.getpid(),
        },
        "machine": {
            "cpus": os.cpu_count(), "load": list(load) if load else None,
            "mem_total": mem.get("MemTotal"), "mem_available": mem.get("MemAvailable"),
        },
        "disk": {
            "data": _disk(Path(data_dir)), "codes": _disk(Path(codes_dir)) if codes_dir else None,
            "accounts_db": accounts_db.stat().st_size if accounts_db.exists() else 0,
        },
        "profile": profile or {},
    }


def _line(name: str, help_text: str, kind: str, samples: list[tuple[dict, float]]) -> list[str]:
    out = [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
    for labels, value in samples:
        tag = ""
        if labels:
            tag = "{" + ",".join(f'{k}="{str(v)}"' for k, v in labels.items()) + "}"
        out.append(f"{name}{tag} {value}")
    return out


def prometheus(*, stats: dict, scan: dict, system: dict, users: int | None, version: str) -> str:
    """Métriques au format texte Prometheus."""
    lines: list[str] = []
    lines += _line("codecenter_info", "Version de l'instance.", "gauge", [({"version": version}, 1)])
    lines += _line("codecenter_uptime_seconds", "Durée de fonctionnement.", "gauge", [({}, system["uptime_s"])])
    lines += _line("codecenter_documents_total", "Documents indexés.", "gauge",
                   [({}, stats.get("documents_total", 0))])
    s = scan.get("scan", {})
    lines += _line("codecenter_index_scan_running", "1 pendant un passage d'indexation.", "gauge",
                   [({}, 1 if s.get("running") else 0)])
    lines += _line("codecenter_index_generation", "Compteur de modifications de l'index.", "counter",
                   [({}, scan.get("generation", 0))])
    for key, help_text in (("seen", "Fichiers vus"), ("indexed", "Fichiers indexés"),
                           ("unchanged", "Fichiers inchangés"), ("failed", "Fichiers en échec"),
                           ("excluded", "Fichiers exclus")):
        lines += _line(f"codecenter_index_scan_{key}", f"{help_text} lors du dernier passage.", "gauge",
                       [({}, s.get(key, 0))])
    lines += _line("codecenter_index_last_scan_seconds", "Durée du dernier passage.", "gauge",
                   [({}, s.get("last_duration_secs", 0))])
    proc, machine = system["process"], system["machine"]
    lines += _line("codecenter_process_resident_bytes", "Mémoire résidente du processus.", "gauge", [({}, proc["rss"])])
    lines += _line("codecenter_process_threads", "Threads du processus.", "gauge", [({}, proc["threads"])])
    if machine.get("load"):
        lines += _line("codecenter_load1", "Charge moyenne sur 1 minute.", "gauge", [({}, machine["load"][0])])
    if machine.get("mem_available") is not None:
        lines += _line("codecenter_memory_available_bytes", "Mémoire disponible.", "gauge",
                       [({}, machine["mem_available"])])
    disk = system["disk"].get("data")
    if disk:
        lines += _line("codecenter_data_disk_free_bytes", "Espace libre du dossier de données.", "gauge",
                       [({}, disk["free"])])
    if users is not None:
        lines += _line("codecenter_users", "Comptes.", "gauge", [({}, users)])
    return "\n".join(lines) + "\n"
