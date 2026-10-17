# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Identité du projet : version cohérente, licence et attribution présentes partout."""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

from fastapi.testclient import TestClient

from icc_ai import about

ROOT = Path(__file__).resolve().parents[2]
SITE = "https://imperial-corporation.pages.dev/icc"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_versions_coherentes():
    expected = read("VERSION").strip()
    assert about.VERSION == expected
    for name in ("api", "engine", "crawler"):
        assert tomllib.loads(read(f"{name}/pyproject.toml"))["project"]["version"] == expected, name
    assert tomllib.loads(read("engine/Cargo.toml"))["package"]["version"] == expected
    lock = read("engine/Cargo.lock")
    assert re.search(r'name = "codecenter-engine"\nversion = "%s"' % re.escape(expected), lock)
    assert f'__version__ = "{expected}"' in read("crawler/src/icc_crawler/__init__.py")
    assert f"## {expected}" in read("CHANGELOG.md")


def test_licence_couvre_les_exigences():
    text = read("LICENSE")
    assert SITE in text and "Imperial Community License" in text
    for needle in ("Article 3 — Attribution obligatoire", "Article 4 — Code ouvert obligatoire",
                   "Article 5 — Usages interdits", "Projet Payant", "Programme Utilisateur", "accès réseau",
                   "Brevets", "Absence de garantie", "Résiliation", "Composants tiers",
                   "PART II — ENGLISH TRANSLATION", "Paid Project", "NOT approved by the Open Source"):
        assert needle in text or needle.lower() in text.lower(), needle


def test_notice_et_metadonnees():
    notice = read("NOTICE")
    assert SITE in notice and "Basé sur Imperial CodeCenter, créé par Imperial" in notice
    for name in ("api", "engine", "crawler"):
        project = tomllib.loads(read(f"{name}/pyproject.toml"))["project"]
        assert project["license"]["text"] == about.LICENSE_ID
        assert project["urls"]["Homepage"] == SITE if "urls" in project else True
    assert tomllib.loads(read("engine/Cargo.toml"))["package"]["license-file"] == "../LICENSE"


def test_en_tetes_de_licence_presents():
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "add_license_headers.py"), "--check"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stdout


def test_route_about_et_openapi():
    import main

    client = TestClient(main.app)
    data = client.get("/api/v1/about").json()
    assert data["website"] == SITE and data["organization"] == "Imperial"
    assert data["license"]["name"] == "Imperial Community License 1.0" and data["version"] == about.VERSION
    schema = client.get("/openapi.json").json()["info"]
    assert schema["version"] == about.VERSION and SITE in schema["description"]
    assert schema["license"]["name"] == about.LICENSE_NAME
