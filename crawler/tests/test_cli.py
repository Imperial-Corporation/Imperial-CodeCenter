# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

import io
import json
import os
import shutil
import signal
import threading
from datetime import datetime, timedelta, timezone

import pytest

from conftest import TOKEN, FakeGitHub, FakeRepo, files_on_disk, json_lines, py_code
from icc_crawler import cli
from icc_crawler.cli import ProgressReporter, build_parser, iso_datetime, main
from icc_crawler.crawler import Stats

GIB = 1024 ** 3


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", TOKEN)


def fake_repo(n=12) -> FakeGitHub:
    return FakeGitHub(FakeRepo("o/r", files={f"src/m{i:02d}.py": py_code(f"m{i:02d}") for i in range(n)}))


def run(argv, fake=None, capsys=None):
    fake = fake or fake_repo()
    code = main(argv, transport=fake.transport())
    out, err = capsys.readouterr() if capsys else ("", "")
    return code, out, err, fake


def base(tmp_path, *extra):
    return ["crawl", "--dest", str(tmp_path / "bib"), "--language", "python", "--pause", "0",
            "--min-free-gb", "0", *extra]


# -- analyse des arguments ----------------------------------------------------------------


def test_parse_defaults_and_flags(tmp_path):
    args = build_parser().parse_args(["crawl", "--dest", str(tmp_path), "--language", "rust", "--language", "go"])
    assert args.language == ["rust", "go"] and args.min_free_gb == 2.0
    assert args.stop_at is None and args.max_minutes is None and args.max_gb is None
    assert args.max_library_gb is None and args.progress_json is False and args.dry_run is False


def test_parse_budget_flags(tmp_path):
    args = build_parser().parse_args([
        "crawl", "--dest", str(tmp_path), "--query", "x", "--stop-at", "2030-01-02T03:04:05+02:00",
        "--max-minutes", "90", "--max-gb", "1.5", "--max-library-gb", "20", "--min-free-gb", "3.5",
        "--progress-json", "--repo", "owner/name"])
    assert args.stop_at == datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=2)))
    assert (args.max_minutes, args.max_gb, args.max_library_gb, args.min_free_gb) == (90.0, 1.5, 20.0, 3.5)
    assert args.progress_json and args.repo == ["owner/name"]


@pytest.mark.parametrize("text,check", [
    ("2030-01-02T03:04:05Z", lambda d: d == datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)),
    ("2030-01-02T03:04:05+00:00", lambda d: d.utcoffset() == timedelta(0)),
    ("2030-01-02T03:04", lambda d: d.tzinfo is not None and d == datetime(2030, 1, 2, 3, 4).astimezone()),
    ("2030-01-02", lambda d: d == datetime(2030, 1, 2).astimezone()),
])
def test_iso_datetime_accepts(text, check):
    assert check(iso_datetime(text))


@pytest.mark.parametrize("flag,value", [
    ("--max-gb", "0"), ("--max-gb", "-1"), ("--max-gb", "abc"), ("--max-gb", "nan"), ("--max-gb", "inf"),
    ("--max-minutes", "0"), ("--max-minutes", "-5"), ("--max-minutes", "x"),
    ("--max-library-gb", "0"), ("--max-library-gb", "-0.5"),
    ("--min-free-gb", "-1"), ("--min-free-gb", "zz"),
    ("--stop-at", "demain"), ("--stop-at", "2030-13-45T99:00"), ("--stop-at", ""),
    ("--max-files", "0"), ("--max-repos", "-3"), ("--max-kb", "0"), ("--max-files-per-repo", "0"),
    ("--min-stars", "-1"), ("--pause", "-1"),
    ("--repo", "pas-un-depot"), ("--repo", "../..//x"), ("--repo", "a/../b"),
])
def test_invalid_values_are_refused_with_code_2(tmp_path, capsys, flag, value):
    code = main(base(tmp_path, flag, value), transport=fake_repo().transport())
    err = capsys.readouterr().err
    assert code == 2 and err.strip()
    assert not (tmp_path / "bib").exists()                      # rien n'a été créé
    assert flag in err or "invalid" in err or "invalide" in err


def test_validation_messages_are_clear(tmp_path, capsys):
    main(base(tmp_path, "--max-gb", "-2"))
    assert "strictement positif" in capsys.readouterr().err
    main(base(tmp_path, "--stop-at", "bientot"))
    assert "ISO 8601" in capsys.readouterr().err


def test_zero_min_free_is_accepted_and_documented(tmp_path):
    assert build_parser().parse_args(["crawl", "--dest", str(tmp_path), "--query", "x", "--min-free-gb", "0"]).min_free_gb == 0


def test_stop_at_in_the_past_is_refused(tmp_path, capsys):
    code, _, err, fake = run(base(tmp_path, "--stop-at", "2020-01-01T00:00:00Z"), capsys=capsys)
    assert code == 2 and "dépassé" in err and fake.log == []


def test_requires_a_selection(tmp_path, capsys):
    code = main(["crawl", "--dest", str(tmp_path)])
    assert code == 2 and "au moins" in capsys.readouterr().err


def test_help_and_version_return_zero(capsys):
    assert main(["--version"]) == 0
    assert main(["crawl", "--help"]) == 0
    assert "--progress-json" in capsys.readouterr().out


def test_unknown_command_or_missing_is_usage_error(capsys):
    assert main([]) == 2
    assert main(["inconnu"]) == 2


# -- crawl bout en bout ------------------------------------------------------------------------


def test_crawl_writes_files_and_prints_summary(tmp_path, capsys):
    code, out, err, fake = run(base(tmp_path), capsys=capsys)
    assert code == 0 and "Fichiers enregistrés : 12" in out and "Fin : termine" in out
    assert len(files_on_disk(tmp_path / "bib")) == 12
    assert TOKEN not in out + err


def test_crawl_without_token_warns_but_never_prints_a_token(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN")
    code, out, err, fake = run(base(tmp_path), capsys=capsys)
    assert code == 0 and "GITHUB_TOKEN absent" in err
    assert all("authorization" not in r.headers for r in fake.log)


def test_dry_run_through_cli_creates_nothing(tmp_path, capsys):
    code, out, err, _ = run(base(tmp_path, "--dry-run"), capsys=capsys)
    assert code == 0 and out.count("[simulation]") == 12
    assert not (tmp_path / "bib").exists()


def test_budget_exit_codes(tmp_path, capsys):
    code, out, err, _ = run(base(tmp_path, "--max-gb", str(3 * len(py_code("m00")) / GIB)), capsys=capsys)
    assert code == 0 and "volume-atteint" in err and len(files_on_disk(tmp_path / "bib")) == 3


def test_max_library_flag(tmp_path, capsys):
    size = len(py_code("m00"))
    code, _, err, _ = run(base(tmp_path, "--max-library-gb", str(4 * size / GIB)), capsys=capsys)
    assert code == 0 and "bibliotheque-pleine" in err and len(files_on_disk(tmp_path / "bib")) == 4


def test_max_minutes_flag_builds_a_budget(tmp_path, monkeypatch, capsys):
    seen = {}
    real = cli.Crawler

    class Spy(real):
        def __init__(self, *a, **kw):
            seen["budget"] = kw["budget"]
            super().__init__(*a, **kw)

    monkeypatch.setattr(cli, "Crawler", Spy)
    stop_at = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(microsecond=0)
    run(base(tmp_path, "--max-minutes", "1.5", "--max-gb", "2", "--max-library-gb", "10",
             "--stop-at", stop_at.isoformat()), capsys=capsys)
    b = seen["budget"]
    assert (b.max_seconds, b.max_bytes, b.max_library_bytes, b.stop_at) == (90.0, 2 * GIB, 10 * GIB, stop_at)
    assert b.min_free_bytes == 0


def test_default_min_free_is_two_gib(tmp_path, monkeypatch, capsys):
    seen = {}
    real = cli.Crawler

    class Spy(real):
        def __init__(self, *a, **kw):
            seen["budget"] = kw["budget"]
            super().__init__(*a, **kw)

    monkeypatch.setattr(cli, "Crawler", Spy)
    argv = ["crawl", "--dest", str(tmp_path / "b"), "--language", "python", "--pause", "0", "--dry-run"]
    run(argv, capsys=capsys)
    assert seen["budget"].min_free_bytes == 2 * GIB


def test_disk_guard_through_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100 * GIB, 99 * GIB, 1 * GIB))
    argv = ["crawl", "--dest", str(tmp_path / "b"), "--language", "python", "--pause", "0", "--min-free-gb", "2"]
    code, _, err, _ = run(argv, capsys=capsys)
    assert code == 1 and "disque-presque-plein" in err
    assert not files_on_disk(tmp_path / "b")


def test_unusable_destination_is_reported(tmp_path, capsys):
    blocker = tmp_path / "fichier"
    blocker.write_text("x")
    code, _, err, _ = run(["crawl", "--dest", str(blocker / "sous"), "--language", "python", "--min-free-gb", "0"],
                          capsys=capsys)
    assert code == 1 and "destination" in err


def test_unexpected_error_gives_exit_1(tmp_path, capsys, monkeypatch):
    from icc_crawler.storage import Store
    monkeypatch.setattr(Store, "save_file", lambda self, **kw: (_ for _ in ()).throw(OSError("plein")))
    code, _, err, _ = run(base(tmp_path), capsys=capsys)
    assert code == 1 and "erreur" in err and "OSError" in err


# -- --progress-json ----------------------------------------------------------------------------


def test_progress_json_is_valid_jsonl_with_final_done(tmp_path, capsys):
    code, out, err, _ = run(base(tmp_path, "--progress-json"), capsys=capsys)
    events = json_lines(out)                        # chaque ligne de stdout est du JSON
    assert code == 0 and len(out.splitlines()) == len(events)
    assert events[0]["event"] == "start" and events[-1]["event"] == "done"
    assert {"dest", "languages", "dry_run", "authenticated", "budget"} <= set(events[0])
    assert events[0]["authenticated"] is True and events[0]["languages"] == ["python"]
    assert set(events[0]["budget"]) == {"stop_at", "max_seconds", "max_bytes", "max_library_bytes", "min_free_bytes"}

    progress = [e for e in events if e["event"] == "progress"]
    assert len(progress) >= 2                       # 12 fichiers : au moins tous les 5
    for e in progress:
        assert set(e) == {"event", "files", "bytes", "repos", "skipped", "elapsed", "phase", "repos_done",
                          "repos_target", "files_target", "repo", "repo_files", "repo_quota", "recent", "errors"}
        assert e["phase"] in {"discovery", "exploring", "done"} and isinstance(e["recent"], list)
        assert all(isinstance(e[k], (int, float)) for k in ("files", "bytes", "repos", "skipped", "elapsed"))
    assert [e["files"] for e in progress] == sorted(e["files"] for e in progress)

    done = events[-1]
    assert done["stop_reason"] == "termine"
    assert done["stats"]["files_saved"] == 12 and progress[-1]["bytes"] <= done["stats"]["bytes_written"]
    assert {"repos_seen", "repos_done", "repos_skipped", "files_saved", "files_skipped", "bytes_written",
            "elapsed_seconds", "stop_reason", "errors"} <= set(done["stats"])
    assert done["stats"]["bytes_written"] == sum(p.stat().st_size for p in files_on_disk(tmp_path / "bib"))
    assert TOKEN not in out + err


def test_progress_json_keeps_stdout_clean(tmp_path, capsys):
    code, out, err, _ = run(["-v", *base(tmp_path, "--progress-json", "--dry-run")], capsys=capsys)
    assert all(line.startswith("{") for line in out.splitlines())
    assert err.count("[simulation]") == 12 and "Fichiers qui seraient enregistrés" in err
    assert "[simulation]" not in out and len(json_lines(out)) == len(out.splitlines())


def test_progress_json_done_on_budget_stop_and_error(tmp_path, capsys, monkeypatch):
    code, out, _, _ = run(base(tmp_path, "--progress-json", "--max-gb", str(2 * len(py_code("m00")) / GIB)), capsys=capsys)
    done = json_lines(out)[-1]
    assert code == 0 and done["event"] == "done" and done["stop_reason"] == "volume-atteint"
    assert done["stats"]["files_saved"] == 2

    from icc_crawler.storage import Store
    monkeypatch.setattr(Store, "save_file", lambda self, **kw: (_ for _ in ()).throw(RuntimeError(f"x {TOKEN}")))
    code, out, err, _ = run(base(tmp_path, "--progress-json"), capsys=capsys)
    done = json_lines(out)[-1]
    assert code == 1 and done["stop_reason"] == "erreur" and TOKEN not in out + err
    assert any("RuntimeError" in e for e in done["stats"]["errors"])


def test_progress_json_token_never_leaks_even_from_server_messages(tmp_path, capsys):
    import httpx
    fake = fake_repo(3)
    fake.override = lambda r: httpx.Response(401, json={"message": f"Bad credentials {TOKEN}"}) \
        if "/git/trees/" in r.url.path else None
    code, out, err, _ = run(base(tmp_path, "--progress-json"), fake=fake, capsys=capsys)
    assert TOKEN not in out + err and "***" in out + err


def test_progress_reporter_throttles_by_time_and_by_files():
    clock = {"t": 0.0}
    stream = io.StringIO()
    reporter = ProgressReporter(stream, monotonic=lambda: clock["t"])
    stats = Stats()

    def lines():
        return [json.loads(l) for l in stream.getvalue().splitlines()]

    for files in range(1, 5):                       # < 5 fichiers, < 1 s : silence
        stats.files_saved = files
        reporter.progress(stats)
    assert lines() == []
    stats.files_saved = 5                           # 5 fichiers : émis
    reporter.progress(stats)
    assert len(lines()) == 1 and lines()[0]["files"] == 5
    reporter.progress(stats)                        # rien de neuf
    assert len(lines()) == 1
    clock["t"] = 0.9
    reporter.progress(stats)
    assert len(lines()) == 1
    clock["t"] = 1.0                                # 1 s écoulée : émis même sans nouveau fichier
    reporter.progress(stats)
    assert len(lines()) == 2
    for files in range(6, 11):
        stats.files_saved = files
        reporter.progress(stats)
    assert [e["files"] for e in lines()] == [5, 5, 10]


def test_progress_reporter_emits_at_once_on_phase_or_repo_change():
    stream = io.StringIO()
    reporter = ProgressReporter(stream, monotonic=lambda: 0.0)
    stats = Stats()

    def lines():
        return [json.loads(l) for l in stream.getvalue().splitlines()]

    stats.phase = "discovery"                       # changement de phase : émis tout de suite
    reporter.progress(stats)
    reporter.progress(stats)                        # inchangé : silence
    stats.phase, stats.repos_target = "exploring", 3
    reporter.progress(stats)
    stats.current_repo = "o/a"                      # nouveau dépôt : émis tout de suite
    reporter.progress(stats)
    stats.remember("o/a/x.py")
    assert [e["phase"] for e in lines()] == ["discovery", "exploring", "exploring"]
    assert lines()[-1]["repo"] == "o/a" and lines()[-1]["repos_target"] == 3


def test_progress_real_run_reports_phases_repo_and_recent_files(tmp_path, capsys):
    code, out, _, _ = run(base(tmp_path, "--progress-json"), capsys=capsys)
    progress = [e for e in json_lines(out) if e["event"] == "progress"]
    assert code == 0 and progress[0]["phase"] == "discovery"
    assert any(e["phase"] == "exploring" and e["repos_target"] >= 1 and e["repo"] for e in progress)
    last = progress[-1]
    assert last["recent"] and len(last["recent"]) <= 6 and last["files_target"] >= last["files"]


def test_progress_reporter_redacts_and_flushes():
    class Stream(io.StringIO):
        flushed = 0

        def flush(self):
            self.flushed += 1

    stream = Stream()
    reporter = ProgressReporter(stream, redact=lambda t: t.replace("secret", "***"))
    reporter.start({"dest": "secret-dir"})
    assert json.loads(stream.getvalue())["dest"] == "***-dir" and stream.flushed == 1


# -- signaux ----------------------------------------------------------------------------------------


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="signaux POSIX")
@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_signal_triggers_graceful_stop_with_exit_0(tmp_path, capsys, sig):
    fake = fake_repo(10)
    fake.hook = lambda r: os.kill(os.getpid(), sig) if fake.count("raw") == 3 and r.url.host.startswith("raw") else None
    before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
    code, out, err, _ = run(base(tmp_path, "--progress-json"), fake=fake, capsys=capsys)
    done = json_lines(out)[-1]
    assert code == 0 and done["stop_reason"] == "arret-demande"
    assert 0 < done["stats"]["files_saved"] < 10 and fake.count("raw") < 10
    assert {s: signal.getsignal(s) for s in before} == before        # handlers restaurés
    # Reprise ensuite : le reste est enregistré, sans doublon.
    fake.hook = None
    code, out, _, _ = run(base(tmp_path, "--progress-json"), fake=fake, capsys=capsys)
    assert code == 0 and json_lines(out)[-1]["stop_reason"] == "termine"
    assert len(files_on_disk(tmp_path / "bib")) == 10


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="signaux POSIX")
def test_signal_wakes_up_rate_limit_sleep(tmp_path, capsys):
    import httpx
    import time
    fake = fake_repo(3)
    state = {"calls": 0}

    def limited(request):
        if "/git/trees/" in request.url.path:
            state["calls"] += 1
            if state["calls"] == 1:
                threading.Timer(0.2, os.kill, (os.getpid(), signal.SIGTERM)).start()
                reset = str(int(time.time()) + 60)
                return httpx.Response(403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": reset})
    fake.override = limited
    started = time.monotonic()
    code, out, _, _ = run(base(tmp_path, "--progress-json"), fake=fake, capsys=capsys)
    assert time.monotonic() - started < 10                              # pas 60 s d'attente
    assert code == 0 and json_lines(out)[-1]["stop_reason"] == "arret-demande"


def test_handlers_not_installed_outside_main_thread(tmp_path, capsys):
    results = {}

    def target():
        results["code"] = main(base(tmp_path, "--dry-run"), transport=fake_repo(2).transport())

    t = threading.Thread(target=target)
    t.start(); t.join()
    assert results["code"] == 0


# -- autres sous-commandes -----------------------------------------------------------------------------


def test_stats_and_export_manifest(tmp_path, capsys):
    run(base(tmp_path), capsys=capsys)
    dest = str(tmp_path / "bib")
    assert main(["stats", "--dest", dest]) == 0
    assert "12 fichiers, 1 dépôts" in capsys.readouterr().out
    out = tmp_path / "m.jsonl"
    assert main(["export-manifest", "--dest", dest, "--output", str(out)]) == 0
    assert len(json_lines(out.read_text(encoding="utf-8"))) == 12
    assert TOKEN not in out.read_text(encoding="utf-8")


def test_stats_on_missing_destination_does_not_create_it(tmp_path, capsys):
    assert main(["stats", "--dest", str(tmp_path / "absent")]) == 1
    assert not (tmp_path / "absent").exists()


def test_disk_check(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: shutil._ntuple_diskusage(100 * GIB, 90 * GIB, 10 * GIB))
    assert main(["disk-check", "--dest", str(tmp_path), "--min-free-gb", "5"]) == 0
    out = capsys.readouterr().out
    assert "10.00 Go" in out and "100.00 Go" in out and "OK" in out
    assert main(["disk-check", "--dest", str(tmp_path / "pas" / "encore"), "--min-free-gb", "10.5"]) == 1
    assert "INSUFFISANT" in capsys.readouterr().out
    assert main(["disk-check", "--dest", str(tmp_path)]) == 0           # défaut 2 Go
    assert main(["disk-check", "--dest", str(tmp_path), "--min-free-gb", "-1"]) == 2
