# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Fichiers de livraison : versions, Docker, workflows GitHub, licence de l'équipe Imperial."""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_version_script():
    spec = importlib.util.spec_from_file_location("icc_version_script", ROOT / "scripts" / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


# -- versions ----------------------------------------------------------------------------------------


def test_toutes_les_versions_sont_coherentes():
    script = _load_version_script()
    versions = script.current_versions()
    assert None not in versions.values(), versions
    assert len(set(versions.values())) == 1, versions
    assert script.check(None) == 0
    assert script.check("v" + versions["VERSION"]) == 0
    assert script.check("v9.9.9") == 1


def test_notes_de_version_extraites_du_changelog(capsys):
    script = _load_version_script()
    version = read("VERSION").strip()
    assert script.notes(version) == 0
    out = capsys.readouterr().out
    assert out.strip() and not out.startswith("##") and "## " not in out
    assert script.notes("0.0.1") == 1


def test_set_refuse_une_version_invalide():
    assert _load_version_script().set_version("1.0") == 1


def test_cargo_lock_suit_la_version_du_moteur():
    lock = read("engine/Cargo.lock")
    assert re.search(r'name = "codecenter-engine"\nversion = "%s"' % re.escape(read("VERSION").strip()), lock)


# -- licence -------------------------------------------------------------------------------------------


def test_licence_copiee_dans_le_moteur_et_declaree():
    assert read("engine/LICENSE") == read("LICENSE")
    assert 'license-file = "LICENSE"' in read("engine/Cargo.toml")


def test_licence_exemption_equipe_imperial():
    text = read("LICENSE")
    assert "1.11 « Équipe Imperial »" in text
    assert "5.8  Cas particulier — Équipe Imperial" in text
    french = text.split("PART II")[0]
    assert "articles 4 et 5" in french and "13.3" in french and "personnelle" in french
    assert "ne s'étend ni à leurs" in re.sub(r"\s+", " ", french)
    assert "Special case — Imperial Team" in text
    assert "Équipe Imperial" in read("NOTICE") and "5.8" in read("docs/licence.md")


# -- Docker ------------------------------------------------------------------------------------------


def test_dockerfile_non_root_et_sans_compilateur_dans_l_image_finale():
    docker = read("Dockerfile")
    final = docker.split("AS runtime", 1)[1]
    assert "USER icc" in final and "HEALTHCHECK" in final and "tini" in final
    assert "rustup" not in final and "build-essential" not in final and "maturin" not in final
    assert "org.opencontainers.image.licenses" in docker and "Imperial-Corporation/Imperial-CodeCenter" in docker
    assert "COPY --from=builder /requirements.txt /wheels/" in docker
    for name in (".env", "*.key"):
        assert name in read(".dockerignore").splitlines()


def test_compose_securise():
    compose = read("compose.yaml")
    assert "POSTGRES_PASSWORD:?" in compose                 # mot de passe obligatoire
    assert "read_only: true" in compose and "cap_drop" in compose and "no-new-privileges" in compose
    assert '"${ICC_BIND:-127.0.0.1}' in compose             # local par défaut
    assert "service_healthy" in compose
    db = compose.split("  app:")[0]
    assert "ports:" not in db                               # la base n'est jamais publiée


def test_entrypoint_syntaxe_et_etapes():
    path = ROOT / "docker" / "entrypoint.sh"
    assert subprocess.run(["sh", "-n", str(path)]).returncode == 0
    text = path.read_text(encoding="utf-8")
    for step in ("secret.key", "pg_isready", "codecenter_schema_migrations", ".examples-installed", 'exec "$@"'):
        assert step in text
    assert "set -eu" in text
    assert path.stat().st_mode & 0o111


def test_migrations_embarquees_dans_l_image():
    assert "COPY config/database/ config/database/" in read("Dockerfile")
    assert list((ROOT / "config" / "database" / "migrations").glob("*.sql"))


# -- GitHub Actions ------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_workflows_valides(name):
    text = read(f".github/workflows/{name}")
    assert text.startswith("# Imperial CodeCenter")
    assert "permissions:" in text and "\t" not in text
    # chaque action est épinglée sur une version majeure
    for action in re.findall(r"uses: ([\w./-]+@[\w.-]+)", text):
        assert re.search(r"@v?\d", action) or action.endswith("@stable"), action


def test_release_publie_image_wheel_et_notes():
    text = read(".github/workflows/release.yml")
    assert 'tags: ["v[0-9]+.[0-9]+.[0-9]+"]' in text
    assert "scripts/version.py check --tag" in text and "scripts/version.py notes" in text
    for needle in ("ghcr.io", "linux/amd64", "linux/arm64", "attest-build-provenance",
                   "maturin-action", "action-gh-release", "packages: write", "${GITHUB_REPOSITORY,,}"):
        assert needle in text, needle


def test_ci_teste_tout():
    text = read(".github/workflows/ci.yml")
    for needle in ("pytest", "cargo test", "scripts/version.py check", "add_license_headers.py --check",
                   "docker compose up", "test_integration_engine", "cmp LICENSE engine/LICENSE"):
        assert needle in text, needle
