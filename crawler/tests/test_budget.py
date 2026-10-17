# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

import shutil
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import TOKEN, FakeClock, FakeGitHub, FakeRepo, crawl, files_on_disk, make_budget, py_code, start
from icc_crawler.crawler import STOP_REASONS, Budget, Clock, directory_size
from icc_crawler.storage import Store

N = 6
FILES = {f"src/m{i}.py": py_code(f"m{i}") for i in range(N)}      # même taille, rang identique
SIZE = len(py_code("m0"))
assert all(len(c) == SIZE for c in FILES.values())


def fake_one_repo() -> FakeGitHub:
    return FakeGitHub(FakeRepo("o/r", files=dict(FILES)))


def done(destination, fake):
    store = Store(destination)
    try:
        return store.repo_done("o/r", fake.repos["o/r"].commit)
    finally:
        store.close()


def resume_and_check(tmp_path, fake, first):
    """Après un arrêt : relance sans budget -> total exact, aucun doublon, aucun retéléchargement."""
    before = fake.raw_paths()
    second = crawl(fake, tmp_path)
    assert second.stop_reason == "termine"
    assert first.files_saved + second.files_saved == N
    assert len(files_on_disk(tmp_path)) == N
    again = fake.raw_paths()[len(before):]
    assert not set(again) & set(before[: first.files_saved])
    assert done(tmp_path, fake)


# -- chaque raison d'arrêt -----------------------------------------------------------


def test_termine(tmp_path):
    stats = crawl(fake_one_repo(), tmp_path)
    assert stats.stop_reason == "termine" and stats.files_saved == N
    assert stats.bytes_written == N * SIZE == sum(p.stat().st_size for p in files_on_disk(tmp_path))


def test_heure_atteinte(tmp_path):
    clock = FakeClock()
    fake = fake_one_repo()
    fake.hook = lambda r: clock.advance(10) if fake.count("raw") and r.url.host.startswith("raw") else None
    budget = make_budget(stop_at=clock.base + timedelta(seconds=25), clock=clock.as_clock())
    stats = crawl(fake, tmp_path, budget=budget)
    assert stats.stop_reason == "heure-atteinte"
    # Le 3e fichier était en vol quand l'heure est passée : contrôle avant écriture -> non enregistré.
    assert stats.files_saved == 2 and fake.count("raw") == 3
    assert not done(tmp_path, fake)
    resume_and_check(tmp_path, fake, stats)


def test_heure_deja_atteinte_ne_fait_aucune_requete(tmp_path):
    clock = FakeClock()
    fake = fake_one_repo()
    stats = crawl(fake, tmp_path, budget=make_budget(stop_at=clock.base, clock=clock.as_clock()))
    assert stats.stop_reason == "heure-atteinte" and fake.log == [] and stats.files_saved == 0


def test_stop_at_naif_est_interprete_comme_heure_locale():
    naive = datetime(2030, 1, 1, 12, 0, 0)
    budget = Budget(stop_at=naive)
    assert budget.stop_at.tzinfo is not None
    assert budget.stop_at == naive.astimezone()


def test_stop_at_avec_fuseau_different(tmp_path):
    clock = FakeClock(datetime(2026, 10, 11, 22, 0, tzinfo=timezone.utc))
    paris = timezone(timedelta(hours=2))
    # 00:30 à Paris le 12 = 22:30 UTC le 11
    budget = make_budget(stop_at=datetime(2026, 10, 12, 0, 30, tzinfo=paris), clock=clock.as_clock())
    budget.start(tmp_path)
    assert budget.check_time() is None
    clock.advance(29 * 60)
    assert budget.check_time() is None
    clock.advance(60)
    assert budget.check_time() == "heure-atteinte"


def test_duree_atteinte(tmp_path):
    clock = FakeClock()
    fake = fake_one_repo()
    fake.hook = lambda r: clock.advance(10) if r.url.host.startswith("raw") else None
    stats = crawl(fake, tmp_path, budget=make_budget(max_seconds=25, clock=clock.as_clock()))
    assert stats.stop_reason == "duree-atteinte" and 0 < stats.files_saved < N
    assert stats.elapsed_seconds == pytest.approx(clock.t)
    assert not done(tmp_path, fake)
    resume_and_check(tmp_path, fake, stats)


def test_volume_atteint_ne_depasse_jamais_et_ne_telecharge_pas_de_trop(tmp_path):
    fake = fake_one_repo()
    stats = crawl(fake, tmp_path, budget=make_budget(max_bytes=2 * SIZE + 5))
    assert stats.stop_reason == "volume-atteint"
    assert stats.files_saved == 2 and stats.bytes_written == 2 * SIZE <= 2 * SIZE + 5
    assert fake.count("raw") == 2                       # le 3e n'est même pas téléchargé
    assert sum(p.stat().st_size for p in files_on_disk(tmp_path)) == stats.bytes_written
    assert not done(tmp_path, fake)
    store = Store(tmp_path)
    assert store.stats()["files"] == 2                  # l'état SQLite colle au disque
    store.close()
    resume_and_check(tmp_path, fake, stats)


def test_volume_exact_est_autorise(tmp_path):
    stats = crawl(fake_one_repo(), tmp_path, budget=make_budget(max_bytes=N * SIZE))
    assert stats.stop_reason == "termine" and stats.files_saved == N


def test_fichier_plus_gros_que_le_budget(tmp_path):
    fake = fake_one_repo()
    stats = crawl(fake, tmp_path, budget=make_budget(max_bytes=SIZE - 1))
    assert stats.stop_reason == "volume-atteint" and stats.files_saved == 0 and fake.count("raw") == 0
    assert files_on_disk(tmp_path) == [] and not done(tmp_path, fake)


def test_volume_compte_seulement_cette_execution(tmp_path):
    fake = fake_one_repo()
    first = crawl(fake, tmp_path, budget=make_budget(max_bytes=2 * SIZE))
    second = crawl(fake, tmp_path, budget=make_budget(max_bytes=2 * SIZE))
    assert (first.files_saved, second.files_saved) == (2, 2)
    assert second.bytes_written == 2 * SIZE


def test_bibliotheque_pleine_compte_les_fichiers_existants_hors_meta(tmp_path):
    (tmp_path / "perso" / "sous").mkdir(parents=True)
    (tmp_path / "perso" / "sous" / "existant.bin").write_bytes(b"x" * 1000)
    (tmp_path / ".crawler").mkdir()
    (tmp_path / ".crawler" / "gros.bin").write_bytes(b"x" * 10_000_000)     # ignoré
    fake = fake_one_repo()
    stats = crawl(fake, tmp_path, budget=make_budget(max_library_bytes=1000 + 3 * SIZE))
    assert stats.stop_reason == "bibliotheque-pleine" and stats.files_saved == 3
    assert directory_size(tmp_path) == 1000 + 3 * SIZE
    assert not done(tmp_path, fake)
    resume_and_check_library(tmp_path, fake, stats)


def resume_and_check_library(tmp_path, fake, first):
    second = crawl(fake, tmp_path)
    assert first.files_saved + second.files_saved == N and second.stop_reason == "termine"


def test_bibliotheque_deja_pleine(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.py").write_bytes(b"x" * 5000)
    fake = fake_one_repo()
    stats = crawl(fake, tmp_path, budget=make_budget(max_library_bytes=5000))
    assert stats.stop_reason == "bibliotheque-pleine" and stats.files_saved == 0 and fake.count("raw") == 0


def test_directory_size_ignores_meta_only_at_root(tmp_path):
    (tmp_path / ".crawler").mkdir()
    (tmp_path / ".crawler" / "a").write_bytes(b"1" * 100)
    (tmp_path / "c").mkdir()
    (tmp_path / "c" / ".crawler").mkdir()
    (tmp_path / "c" / ".crawler" / "b").write_bytes(b"1" * 7)
    (tmp_path / "f").write_bytes(b"1" * 3)
    assert directory_size(tmp_path) == 10
    assert directory_size(tmp_path, skip_meta=False) == 110
    assert directory_size(tmp_path / "inexistant") == 0


def fake_disk(monkeypatch, free_fn):
    calls = []

    def disk_usage(path):
        calls.append(path)
        return shutil._ntuple_diskusage(10**12, 0, free_fn())

    monkeypatch.setattr(shutil, "disk_usage", disk_usage)
    return calls


def test_disque_presque_plein_dynamique(tmp_path, monkeypatch):
    minimum = 1000
    calls = fake_disk(monkeypatch, lambda: minimum + 2 * SIZE - directory_size(tmp_path))
    fake = fake_one_repo()
    stats = crawl(fake, tmp_path, budget=Budget(min_free_bytes=minimum))
    assert stats.stop_reason == "disque-presque-plein" and stats.files_saved == 2
    assert calls and not done(tmp_path, fake)
    # Le disque est libéré : reprise normale, sans doublon.
    monkeypatch.undo()
    resume_and_check(tmp_path, fake, stats)


def test_disque_frontiere_taille_du_fichier_comptee(tmp_path, monkeypatch):
    fake_disk(monkeypatch, lambda: 1000 + SIZE)             # reste exactement le seuil après écriture
    stats = crawl(fake_one_repo(), tmp_path / "a", budget=Budget(min_free_bytes=1000))
    assert stats.stop_reason == "termine" and stats.files_saved == N       # libre statique : tout passe
    fake_disk(monkeypatch, lambda: 1000 + SIZE - 1)         # un octet de moins : refusé avant écriture
    fake = fake_one_repo()
    stats = crawl(fake, tmp_path / "b", budget=Budget(min_free_bytes=1000))
    assert stats.stop_reason == "disque-presque-plein" and stats.files_saved == 0
    assert files_on_disk(tmp_path / "b") == [] and fake.count("raw") == 0


def test_disque_deja_sous_le_seuil_avant_toute_ecriture(tmp_path, monkeypatch):
    fake_disk(monkeypatch, lambda: 10)
    stats = crawl(fake_one_repo(), tmp_path, budget=Budget(min_free_bytes=2 * 1024**3))
    assert stats.stop_reason == "disque-presque-plein" and stats.files_saved == 0


def test_min_free_zero_desactive_la_garde(tmp_path, monkeypatch):
    calls = fake_disk(monkeypatch, lambda: 0)
    stats = crawl(fake_one_repo(), tmp_path, budget=Budget(min_free_bytes=0))
    assert stats.stop_reason == "termine" and calls == []


def test_defaut_min_free_est_2_go():
    assert Budget().min_free_bytes == 2 * 1024**3


def test_disque_destination_inexistante_utilise_le_parent(tmp_path, monkeypatch):
    calls = fake_disk(monkeypatch, lambda: 10**12)
    Budget().check_write(10, 0, tmp_path / "a" / "b" / "c")
    assert calls == [tmp_path.absolute()]


def test_arret_demande_avant_demarrage(tmp_path):
    fake = fake_one_repo()
    run = start(fake, tmp_path)
    run.crawler.request_stop()
    stats = run.crawler.run()
    assert stats.stop_reason == "arret-demande" and fake.log == [] and stats.files_saved == 0


def test_arret_demande_pendant_le_crawl(tmp_path):
    fake = fake_one_repo()
    run = start(fake, tmp_path)
    fake.hook = lambda r: run.crawler.request_stop() if fake.count("raw") == 2 else None
    stats = run.crawler.run()
    assert stats.stop_reason == "arret-demande" and stats.files_saved == 1   # le 2e, en vol, n'est pas écrit
    assert run.crawler.stop_requested and not run.store.repo_done("o/r", fake.repos["o/r"].commit)
    run.client.close(); run.store.close()
    resume_and_check(tmp_path, fake, stats)


def test_arret_demande_prioritaire_sur_les_budgets(tmp_path):
    clock = FakeClock()
    run = start(fake_one_repo(), tmp_path, budget=make_budget(stop_at=clock.base, clock=clock.as_clock()))
    run.crawler.request_stop()
    assert run.crawler.run().stop_reason == "arret-demande"


def test_arret_demande_depuis_un_autre_thread(tmp_path):
    import threading
    fake = fake_one_repo()
    run = start(fake, tmp_path)
    timer = threading.Timer(0.0, run.crawler.request_stop)
    timer.start(); timer.join()
    assert run.crawler.run().stop_reason == "arret-demande"


def test_limite_fichiers(tmp_path):
    fake = FakeGitHub(FakeRepo("o/a", files={f"s/a{i}.py": py_code(f"a{i}") for i in range(4)}),
                      FakeRepo("o/b", files={f"s/b{i}.py": py_code(f"b{i}") for i in range(4)}))
    stats = crawl(fake, tmp_path, max_files=3, max_files_per_repo=10)
    assert stats.stop_reason == "limite-fichiers" and stats.files_saved == 3
    store = Store(tmp_path)
    assert not store.repo_done("o/a", fake.repos["o/a"].commit)     # interrompu au milieu : à reprendre
    store.close()
    more = crawl(fake, tmp_path, max_files=100, max_files_per_repo=10)
    assert more.stop_reason == "termine" and 3 + more.files_saved == 8 and len(files_on_disk(tmp_path)) == 8


def test_limite_fichiers_exacte_est_termine(tmp_path):
    fake = FakeGitHub(FakeRepo("o/a", files={f"s/a{i}.py": py_code(f"a{i}") for i in range(3)}))
    stats = crawl(fake, tmp_path, max_files=3)
    assert stats.stop_reason == "termine" and stats.files_saved == 3


def test_limite_fichiers_entre_deux_depots(tmp_path):
    fake = FakeGitHub(FakeRepo("o/a", stars=9, files={"s/a.py": py_code("a")}),
                      FakeRepo("o/b", stars=1, files={"s/b.py": py_code("b")}))
    stats = crawl(fake, tmp_path, max_files=1)
    assert stats.stop_reason == "limite-fichiers" and stats.files_saved == 1 and stats.repos_done == 1


def test_limite_de_debit(tmp_path):
    fake = fake_one_repo()
    reset = 1_000_000 + 10_000
    fake.override = lambda r: httpx.Response(
        403, json={"message": "rate limit"},
        headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset)}) if "/git/trees/" in r.url.path else None
    run = start(fake, tmp_path, client_kwargs={"max_wait": 100})
    stats = run.crawler.run()
    assert stats.stop_reason == "limite-de-debit" and stats.files_saved == 0
    assert any("plafond" in e for e in stats.errors) and TOKEN not in " ".join(stats.errors)
    assert not run.store.repo_done("o/r", fake.repos["o/r"].commit)
    assert run.sleeps == []


def test_limite_de_debit_en_cours_de_route_conserve_le_travail_fait(tmp_path):
    fake = fake_one_repo()
    reset = 1_000_000 + 10_000

    def limit_after_two(request):
        if request.url.host.startswith("raw") and fake.count("raw") > 2:
            return httpx.Response(429, headers={"retry-after": "5000"})
    fake.override = limit_after_two
    stats = crawl(fake, tmp_path, client_kwargs={"max_wait": 100})
    assert stats.stop_reason == "limite-de-debit" and stats.files_saved == 2
    assert len(files_on_disk(tmp_path)) == 2 and not done(tmp_path, fake)
    fake.override = None
    resume_and_check(tmp_path, fake, stats)


def test_erreur_inattendue_masque_le_jeton_et_garde_un_etat_coherent(tmp_path, monkeypatch):
    fake = fake_one_repo()
    original = Store.save_file
    calls = {"n": 0}

    def flaky(self, **kwargs):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError(f"disque plein ({TOKEN})")
        return original(self, **kwargs)

    monkeypatch.setattr(Store, "save_file", flaky)
    stats = crawl(fake, tmp_path)
    assert stats.stop_reason == "erreur" and stats.files_saved == 2
    assert any("OSError" in e and "disque plein" in e for e in stats.errors)
    assert TOKEN not in " ".join(stats.errors)
    assert not done(tmp_path, fake)
    monkeypatch.undo()
    resume_and_check(tmp_path, fake, stats)


def test_dry_run_respecte_le_temps_mais_pas_les_budgets_d_ecriture(tmp_path):
    fake = fake_one_repo()
    stats = crawl(fake, tmp_path / "x", use_store=False, dry_run=True, budget=make_budget(max_bytes=1))
    assert stats.stop_reason == "termine" and stats.files_saved == N and stats.bytes_written == 0

    clock = FakeClock()
    fake.hook = lambda r: clock.advance(10) if r.url.host.startswith("raw") else None
    stats = crawl(fake, tmp_path / "y", use_store=False, dry_run=True,
                  budget=make_budget(max_seconds=25, clock=clock.as_clock()))
    assert stats.stop_reason == "duree-atteinte" and not (tmp_path / "y").exists()


def test_stats_champs_et_serialisation(tmp_path):
    import json
    stats = crawl(fake_one_repo(), tmp_path)
    data = stats.as_dict()
    assert {"stop_reason", "bytes_written", "elapsed_seconds", "files_saved", "files_skipped",
            "repos_seen", "repos_done", "repos_skipped", "errors"} <= set(data)
    json.dumps(data)
    assert stats.elapsed_seconds >= 0


def test_toutes_les_raisons_documentees():
    assert set(STOP_REASONS) == {
        "termine", "heure-atteinte", "duree-atteinte", "volume-atteint", "bibliotheque-pleine",
        "disque-presque-plein", "arret-demande", "limite-fichiers", "limite-de-debit", "erreur"}


def test_budget_unitaire_priorite_heure_puis_duree(tmp_path):
    clock = FakeClock()
    budget = Budget(stop_at=clock.base + timedelta(seconds=10), max_seconds=10, min_free_bytes=0, clock=clock.as_clock())
    budget.start(tmp_path)
    clock.advance(10)
    assert budget.check_time() == "heure-atteinte"
    only = Budget(max_seconds=10, min_free_bytes=0, clock=clock.as_clock())
    only.start(tmp_path)
    clock.advance(9.9)
    assert only.check_time() is None
    clock.advance(0.1)
    assert only.check_time() == "duree-atteinte" and only.elapsed() == pytest.approx(10)


def test_budget_sans_limite_ne_s_arrete_jamais(tmp_path):
    budget = Budget(min_free_bytes=0)
    budget.start(tmp_path)
    assert budget.check_time() is None and budget.check_write(10**15, 10**15, tmp_path) is None


def test_budget_describe_serialisable():
    import json
    clock = FakeClock()
    text = json.dumps(Budget(stop_at=clock.base, max_seconds=5, max_bytes=7, clock=clock.as_clock()).describe())
    assert "2026-10-11T22:00:00+00:00" in text


def test_progression_notifiee(tmp_path):
    seen = []
    fake = fake_one_repo()
    run = start(fake, tmp_path)
    run.crawler._on_progress = lambda s: seen.append(s.files_saved)
    run.crawler.run()
    assert seen and seen[-1] == N and seen == sorted(seen)
