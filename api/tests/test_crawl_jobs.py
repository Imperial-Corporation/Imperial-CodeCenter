# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""CrawlManager : sous-processus factice, planification, arrêt, reprise, jeton chiffré."""

import asyncio
import json
import os
import stat
import sys
import textwrap
import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from conftest import run
from icc_ai import crawl_jobs
from icc_ai.config.crypto import SecretBox
from icc_ai.crawl_jobs import CrawlManager, CrawlRequest, build_command, parse_when
from icc_ai.errors import ConfigError

TOKEN = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"

FAKE_CRAWLER = textwrap.dedent('''
    import json, os, signal, sys, time

    mode, env_out = sys.argv[1], sys.argv[2]


    def emit(**event):
        print(json.dumps(event), flush=True)


    with open(env_out, "w", encoding="utf-8") as handle:
        json.dump(dict(os.environ), handle)

    emit(event="start")
    print("ligne qui n'est pas du JSON", flush=True)
    if mode == "ok":
        emit(event="progress", files=3, bytes=1200, repos=1, skipped=2, elapsed=1.5)
        sys.stderr.write("jeton=" + os.environ.get("GITHUB_TOKEN", "") + "\\n")
        sys.stderr.flush()
        emit(event="done", stop_reason="termine",
             stats={"files_saved": 3, "bytes_written": 1200, "repos_done": 1,
                    "files_skipped": 2, "elapsed_seconds": 1.5})
    elif mode == "fail":
        sys.stderr.write("boum\\n")
        sys.exit(3)
    elif mode == "done_bad_exit":
        emit(event="done", stop_reason="erreur", stats={})
        sys.exit(2)
    elif mode == "loop_handler":
        def stop(signum, frame):
            emit(event="done", stop_reason="arret-demande", stats={"files_saved": 1})
            sys.exit(0)
        signal.signal(signal.SIGTERM, stop)
        emit(event="progress", files=1, bytes=10, repos=1, skipped=0, elapsed=0.1)
        while True:
            time.sleep(0.05)
    elif mode == "loop_plain":
        emit(event="progress", files=1, bytes=10, repos=1, skipped=0, elapsed=0.1)
        while True:
            time.sleep(0.05)
''')


@pytest.fixture
def box():
    return SecretBox(Fernet.generate_key())


@pytest.fixture
def fake(tmp_path, monkeypatch):
    """Remplace la ligne de commande du crawler par un script factice (jamais de réseau)."""
    script = tmp_path / "faux_crawler.py"
    script.write_text(FAKE_CRAWLER, encoding="utf-8")
    env_out = tmp_path / "env.json"
    state = {"mode": "ok", "commands": []}

    def fake_build(request, destination, stop_at):
        state["commands"].append((request, destination, stop_at))
        return [sys.executable, str(script), state["mode"], str(env_out)]

    monkeypatch.setattr(crawl_jobs, "build_command", fake_build)
    state["env_out"] = env_out
    return state


@pytest.fixture
def manager(tmp_path, box):
    return CrawlManager(tmp_path / "data", tmp_path / "codes", box)


async def wait_for(predicate, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("délai dépassé en attendant la condition")


async def wait_state(manager, *states):
    await wait_for(lambda: manager._job and manager._job["state"] in states)
    return manager._job


def soon(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


# --------------------------------------------------------------------------
# CrawlRequest et dates
# --------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"languages": ["klingon"]},
    {"languages": ["python", "cobol"]},
    {"languages": ["python"] * 11},
    {"repos": ["pas-un-depot"]},
    {"repos": ["a/b/c"]},
    {"repos": ["a b/c"]},
    {"repos": ["../etc/passwd"]},
    {"repos": ["x/y"] * 21},
    {"topics": ["a;rm -rf /"]},
    {"topics": ["a$(id)"]},
    {"topics": ["t"] * 6},
    {"query": "x; rm"},
    {"query": "a" * 81},
    {"min_stars": -1}, {"min_stars": 1_000_001},
    {"max_repos": 0}, {"max_repos": 501},
    {"max_files_per_repo": 0}, {"max_files_per_repo": 201},
    {"max_files": 0}, {"max_files": 20_001},
    {"max_minutes": 0}, {"max_minutes": -5}, {"max_minutes": 60 * 24 * 7 + 1},
    {"max_gb": 0}, {"max_gb": 10_001},
    {"max_library_gb": 0}, {"max_library_gb": 100_001},
    {"min_free_gb": 0.1}, {"min_free_gb": 10_001},
    {"start_at": "demain"},
    {"stop_at": "pas une date"},
    {"stop_at": "2001-01-01T00:00:00Z"},
    {"stop_at": "2000-01-01T00:00:00+00:00", "start_at": "2001-01-01T00:00:00+00:00"},
])
def test_crawl_request_refuse(payload):
    with pytest.raises(ValidationError):
        CrawlRequest(**payload)


def test_stop_at_avant_start_at_refuse():
    with pytest.raises(ValidationError):
        CrawlRequest(start_at=soon(3600), stop_at=soon(1800))
    assert CrawlRequest(start_at=soon(1800), stop_at=soon(3600))


def test_crawl_request_valeurs_nettoyees():
    request = CrawlRequest(languages=[" Python ", "RUST", "python", ""], query="  web api  ",
                           topics=[" cli "], repos=[" a/b "])
    assert request.languages == ["python", "rust"] and request.query == "web api"
    assert request.topics == ["cli"] and request.repos == ["a/b"]
    assert CrawlRequest(languages=[]).languages == ["python"]
    assert CrawlRequest(query="   ").query is None


def test_parse_when():
    assert parse_when(None) is None and parse_when("") is None
    assert parse_when("2030-01-01T10:00:00Z") == datetime(2030, 1, 1, 10, tzinfo=timezone.utc)
    assert parse_when("2030-01-01T12:00:00+02:00") == datetime(2030, 1, 1, 10, tzinfo=timezone.utc)
    assert parse_when("2030-01-01T10:00:00").tzinfo == timezone.utc  # naïve : heure locale, ramenée en UTC
    with pytest.raises(ValueError):
        parse_when("n'importe quoi", "start_at")


def test_build_command_liste_d_arguments_sans_shell(tmp_path):
    request = CrawlRequest(languages=["python", "go"], query="web", topics=["cli"], repos=["a/b"],
                           max_minutes=5, max_gb=2, max_library_gb=9)
    stop = datetime(2030, 1, 1, tzinfo=timezone.utc)
    command = build_command(request, tmp_path / "gh", stop)
    assert command[0] == sys.executable and command[1:3] == ["-m", "icc_crawler"]
    assert command.count("--language") == 2 and command[command.index("--query") + 1] == "web"
    assert command[command.index("--stop-at") + 1] == stop.isoformat()
    assert command[command.index("--max-minutes") + 1] == "5.0"
    assert command[command.index("--dest") + 1] == str(tmp_path / "gh")
    assert "--progress-json" in command and all(isinstance(a, str) for a in command)
    minimal = build_command(CrawlRequest(), tmp_path, None)
    assert "--stop-at" not in minimal and "--max-gb" not in minimal and "--query" not in minimal


# --------------------------------------------------------------------------
# Jeton chiffré
# --------------------------------------------------------------------------

def test_jeton_chiffre_sur_disque_jamais_en_clair(manager, tmp_path, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert manager.token_source() == "aucun"
    manager.save_token(f"  {TOKEN}  ")
    path = tmp_path / "data" / "crawler-token.enc"
    assert path.is_file() and TOKEN not in path.read_text()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not (tmp_path / "data" / "crawler-token.tmp").exists()
    assert manager.token_source() == "enregistre" and manager._token() == TOKEN
    exposed = json.dumps(manager.status())
    assert TOKEN not in exposed
    assert not any(TOKEN in p.read_text(errors="ignore") for p in (tmp_path / "data").rglob("*") if p.is_file())


def test_jeton_invalide_ou_sans_chiffrement(tmp_path, manager):
    for bad in ("court", "avec espace " * 3, "ghp_" + "é" * 30, "x" * 300, "a;b" + "c" * 30):
        with pytest.raises(ValueError):
            manager.save_token(bad)
    assert manager.token_source() != "enregistre"
    nobox = CrawlManager(tmp_path / "d2", tmp_path / "c2", None)
    with pytest.raises(ConfigError):
        nobox.save_token(TOKEN)
    manager.save_token(None)  # vide : sans effet
    manager.save_token("   ")
    assert manager.token_source() != "enregistre"


def test_jeton_effacement_et_environnement(manager, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env_token_value_12345678")
    assert manager.token_source() == "environnement" and manager._token() == "env_token_value_12345678"
    manager.save_token(TOKEN)
    assert manager.token_source() == "enregistre" and manager._token() == TOKEN
    manager.save_token(None, clear=True)
    manager.save_token(None, clear=True)  # idempotent
    assert manager.token_source() == "environnement"


def test_jeton_illisible_retombe_sur_l_environnement(tmp_path, box, monkeypatch):
    CrawlManager(tmp_path / "data", tmp_path / "c", box).save_token(TOKEN)
    other = CrawlManager(tmp_path / "data", tmp_path / "c", SecretBox(Fernet.generate_key()))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert other._token() is None
    monkeypatch.setenv("GITHUB_TOKEN", "env_token_value_12345678")
    assert other._token() == "env_token_value_12345678"


# --------------------------------------------------------------------------
# Cycle de vie
# --------------------------------------------------------------------------

def test_execution_terminee(manager, fake, tmp_path):
    async def scenario():
        started = manager.start(CrawlRequest())
        assert started["state"] == "scheduled" and manager.status()["active"]
        await wait_state(manager, "finished", "failed")
        return manager.status()

    status = run(scenario())
    job = status["job"]
    assert job["state"] == "finished" and job["stop_reason"] == "termine"
    assert job["message"] == "Collecte terminée." and job["ended_at"] and job["started_at"]
    assert job["progress"] == {"files": 3, "bytes": 1200, "repos": 1, "skipped": 2, "elapsed": 1.5}
    assert status["active"] is False and status["history"][-1]["id"] == job["id"]
    request, destination, stop_at = fake["commands"][0]
    assert destination == tmp_path / "codes" / "github" and destination.is_dir() and stop_at is None
    saved = json.loads((tmp_path / "data" / "crawler-state.json").read_text())
    assert saved["job"]["state"] == "finished"


def test_environnement_du_sous_processus_reduit(manager, fake, monkeypatch):
    for name in ("CODECENTER_SECRET_KEY", "DATABASE_URL", "CODECENTER_ADMIN_TOKEN", "OPENAI_API_KEY",
                 "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "valeur-secrete")
    monkeypatch.setenv("GITHUB_TOKEN", "env_token_value_12345678")
    manager.save_token(TOKEN)

    async def scenario():
        manager.start(CrawlRequest())
        await wait_state(manager, "finished", "failed")

    run(scenario())
    child = json.loads(fake["env_out"].read_text())
    assert not any("valeur-secrete" in v for v in child.values())
    for name in ("CODECENTER_SECRET_KEY", "DATABASE_URL", "CODECENTER_ADMIN_TOKEN", "OPENAI_API_KEY"):
        assert name not in child
    assert child["GITHUB_TOKEN"] == TOKEN  # le jeton enregistré prime sur l'environnement
    assert child["PYTHONUNBUFFERED"] == "1" and child["HOME"] == str(manager.data_dir)
    assert set(child) <= {"PATH", "HOME", "PYTHONUNBUFFERED", "PYTHONPATH", "LANG", "GITHUB_TOKEN", "LC_CTYPE"}


def test_journal_sans_jeton(manager, fake, tmp_path):
    manager.save_token(TOKEN)

    async def scenario():
        manager.start(CrawlRequest())
        await wait_state(manager, "finished", "failed")

    run(scenario())
    job = manager.status()["job"]
    assert any("jeton=***" in line for line in job["log"])
    everything = json.dumps(manager.status()) + (tmp_path / "data" / "crawler-state.json").read_text()
    assert TOKEN not in everything


def test_echec_sans_evenement_final(manager, fake):
    fake["mode"] = "fail"

    async def scenario():
        manager.start(CrawlRequest())
        await wait_state(manager, "failed", "finished")

    run(scenario())
    job = manager.status()["job"]
    assert job["state"] == "failed" and job["stop_reason"] == "erreur" and "code 3" in job["message"]
    assert "boum" in job["log"]


def test_code_de_sortie_non_nul_malgre_done(manager, fake):
    fake["mode"] = "done_bad_exit"

    async def scenario():
        manager.start(CrawlRequest())
        await wait_state(manager, "failed", "finished")

    run(scenario())
    assert manager.status()["job"]["state"] == "failed"


def test_commande_introuvable_donne_un_echec_sans_fuite(manager, monkeypatch):
    monkeypatch.setattr(crawl_jobs, "build_command", lambda *a: ["/chemin/inexistant/crawler"])

    async def scenario():
        manager.start(CrawlRequest())
        await wait_state(manager, "failed")

    run(scenario())
    job = manager.status()["job"]
    assert job["state"] == "failed" and "/chemin/inexistant" not in json.dumps(job)


def test_un_seul_travail_a_la_fois(manager, fake):
    fake["mode"] = "loop_handler"

    async def scenario():
        manager.start(CrawlRequest())
        with pytest.raises(ValueError, match="déjà"):
            manager.start(CrawlRequest())
        await manager.stop()
        await wait_state(manager, "stopped", "failed", "cancelled")
        manager.start(CrawlRequest())  # libre de nouveau
        await manager.shutdown()

    run(scenario())


def test_arret_propre_sigterm(manager, fake):
    fake["mode"] = "loop_handler"

    async def scenario():
        manager.start(CrawlRequest())
        await wait_for(lambda: manager._job["progress"])
        returned = await manager.stop()
        assert returned["state"] == "stopping" and manager.status()["active"]
        return await wait_state(manager, "stopped", "failed", "finished")

    job = run(scenario())
    assert job["state"] == "stopped" and job["stop_reason"] == "arret-demande"
    assert manager.status()["active"] is False


def test_arret_sans_evenement_final_reste_stopped(manager, fake):
    """Le crawler meurt sur SIGTERM sans émettre `done` : c'est un arrêt demandé, pas un échec."""
    fake["mode"] = "loop_plain"

    async def scenario():
        manager.start(CrawlRequest())
        await wait_for(lambda: manager._job["progress"])
        await manager.stop()
        return await wait_state(manager, "stopped", "failed", "finished")

    assert run(scenario())["state"] == "stopped"


def test_arret_immediatement_apres_le_demarrage(manager, fake):
    """Demande d'arrêt reçue avant que le processus existe : elle ne doit pas être perdue."""
    fake["mode"] = "loop_plain"

    async def scenario():
        manager.start(CrawlRequest())
        await wait_state(manager, "running")
        await manager.stop()
        return await wait_state(manager, "stopped", "failed", "finished")

    assert run(scenario())["state"] == "stopped"


def test_arret_sans_travail(manager):
    async def scenario():
        assert await manager.stop() is None
        await manager.shutdown()

    run(scenario())


def test_arret_du_service_termine_le_sous_processus(manager, fake):
    fake["mode"] = "loop_plain"

    async def scenario():
        manager.start(CrawlRequest())
        await wait_for(lambda: manager._job["progress"])
        process = manager._process
        await manager.shutdown()
        return process.returncode

    assert run(scenario()) is not None


# --------------------------------------------------------------------------
# Planification et reprise
# --------------------------------------------------------------------------

def test_planification_puis_annulation(manager, fake):
    async def scenario():
        job = manager.start(CrawlRequest(start_at=soon(3600)))
        assert job["state"] == "scheduled" and job["start_at"]
        await asyncio.sleep(0.1)
        assert manager._process is None and fake["commands"] == []
        with pytest.raises(ValueError, match="déjà"):
            manager.start(CrawlRequest())
        cancelled = await manager.stop()
        assert cancelled["state"] == "cancelled" and cancelled["stop_reason"] == "arret-demande"
        assert manager.status()["active"] is False
        manager.start(CrawlRequest())
        await wait_state(manager, "finished", "failed")

    run(scenario())
    assert len(manager.status()["history"]) == 2
    assert [h["state"] for h in manager.status()["history"]] == ["cancelled", "finished"]


def test_planification_a_l_heure(manager, fake):
    async def scenario():
        job = manager.start(CrawlRequest(start_at=soon(1.0)))
        assert job["state"] == "scheduled"
        await asyncio.sleep(0.3)
        assert manager._job["state"] == "scheduled" and not fake["commands"]
        await wait_state(manager, "finished", "failed")

    started = time.monotonic()
    run(scenario())
    assert time.monotonic() - started >= 0.9
    assert manager.status()["job"]["state"] == "finished"


def test_heure_de_demarrage_passee_refusee(manager, fake):
    async def scenario():
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with pytest.raises(ValueError, match="passée") as err:
            manager.start(CrawlRequest(start_at=past))
        assert not isinstance(err.value, crawl_jobs.JobBusyError)
        # Dans la tolérance de deux minutes : démarrage immédiat.
        recent = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        job = manager.start(CrawlRequest(start_at=recent))
        assert job["start_at"] is None
        await wait_state(manager, "finished", "failed")

    run(scenario())


def test_heure_d_arret_depassee_avant_le_demarrage(manager, fake):
    async def scenario():
        start = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
        stop = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        manager.start(CrawlRequest(start_at=start, stop_at=stop))
        await wait_state(manager, "finished", "failed")

    run(scenario())
    job = manager.status()["job"]
    assert job["state"] == "finished" and job["stop_reason"] == "heure-atteinte" and not fake["commands"]


def test_stop_at_transmis_au_crawler(manager, fake):
    stop = soon(3600)

    async def scenario():
        manager.start(CrawlRequest(stop_at=stop))
        await wait_state(manager, "finished", "failed")

    run(scenario())
    assert fake["commands"][0][2] == parse_when(stop)


def test_crawler_introuvable(manager, monkeypatch):
    monkeypatch.setattr(crawl_jobs.crawler_bridge, "ensure_importable", lambda: False)

    async def scenario():
        with pytest.raises(ConfigError):
            manager.start(CrawlRequest())

    run(scenario())
    assert manager.status()["available"] is False and manager.status()["job"] is None


def test_reprise_d_une_planification_apres_redemarrage(tmp_path, box, fake):
    async def scenario():
        first = CrawlManager(tmp_path / "data", tmp_path / "codes", box)
        first.start(CrawlRequest(start_at=soon(3600)))
        await first.shutdown()  # « arrêt du service » : la planification reste sur disque

        second = CrawlManager(tmp_path / "data", tmp_path / "codes", box)
        assert second.status()["job"]["state"] == "scheduled" and second.status()["active"]
        with pytest.raises(ValueError, match="déjà"):
            second.start(CrawlRequest())
        second.resume()
        assert second._task is not None and not second._task.done()
        await second.stop()
        assert second.status()["job"]["state"] == "cancelled"

        third = CrawlManager(tmp_path / "data", tmp_path / "codes", box)
        third.resume()  # rien à réarmer
        assert third._task is None

    run(scenario())


def test_reprise_immediate_lance_le_travail(tmp_path, box, fake):
    async def scenario():
        first = CrawlManager(tmp_path / "data", tmp_path / "codes", box)
        first.start(CrawlRequest())
        first._task.cancel()  # le service s'arrête avant le lancement
        await asyncio.gather(first._task, return_exceptions=True)
        second = CrawlManager(tmp_path / "data", tmp_path / "codes", box)
        second.resume()
        await wait_state(second, "finished", "failed")
        return second

    assert run(scenario()).status()["job"]["state"] == "finished"


def test_reprise_planification_manquee(tmp_path, box, fake):
    async def scenario():
        first = CrawlManager(tmp_path / "data", tmp_path / "codes", box)
        job = first.start(CrawlRequest(start_at=soon(3600)))
        await first.shutdown()
        state = tmp_path / "data" / "crawler-state.json"
        data = json.loads(state.read_text())
        data["job"]["start_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        state.write_text(json.dumps(data))
        second = CrawlManager(tmp_path / "data", tmp_path / "codes", box)
        second.resume()
        return job["id"], second

    job_id, second = run(scenario())
    assert second._job["state"] == "missed" and second.status()["active"] is False
    assert second.status()["history"][-1]["id"] == job_id and not fake["commands"]


@pytest.mark.parametrize("state", ["running", "stopping"])
def test_travail_en_cours_interrompu_par_un_redemarrage(tmp_path, box, state):
    data = tmp_path / "data"
    data.mkdir()
    (data / "crawler-state.json").write_text(json.dumps({
        "job": {"id": "abc", "state": state, "request": {}, "log": []}, "history": []}))
    manager = CrawlManager(data, tmp_path / "codes", box)
    status = manager.status()
    assert status["active"] is False and status["job"] is None
    assert status["history"][-1]["state"] == "interrupted" and "redémarrage" in status["history"][-1]["message"]


def test_etat_illisible_ignore(tmp_path, box):
    data = tmp_path / "data"
    data.mkdir()
    (data / "crawler-state.json").write_text("{corrompu")
    manager = CrawlManager(data, tmp_path / "codes", box)
    assert manager.status()["job"] is None and manager.status()["history"] == []


def test_historique_borne(manager, fake):
    async def scenario():
        for _ in range(13):
            manager.start(CrawlRequest())
            await wait_state(manager, "finished", "failed")

    run(scenario())
    saved = json.loads((manager.data_dir / "crawler-state.json").read_text())
    assert len(saved["history"]) == 10 and len(manager.status()["history"]) == 5


def test_info_disque(manager):
    info = manager.disk_info()
    assert set(info) == {"free_gb", "total_gb"} and info["total_gb"] >= info["free_gb"] >= 0
