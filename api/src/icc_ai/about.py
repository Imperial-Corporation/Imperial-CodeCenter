# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Identité du projet : version, origine, licence (affichées par l'API et l'interface)."""

from __future__ import annotations

NAME = "Imperial CodeCenter"
VERSION = "1.0.4"
ORGANIZATION = "Imperial"
WEBSITE = "https://imperial-corporation.pages.dev/icc"
REPOSITORY = "https://github.com/Imperial-Corporation/Imperial-CodeCenter"
LICENSE_NAME = "Imperial Community License 1.0"
LICENSE_ID = "LicenseRef-Imperial-Community-1.0"
CREDITS = {
    "lead_developer": "yolezz",
    "organization": "Imperial",
    "original_idea": "HyperDemoned",
}
ATTRIBUTION = f"Basé sur {NAME}, créé par {ORGANIZATION} — {WEBSITE}"


def about() -> dict:
    return {
        "name": NAME,
        "version": VERSION,
        "organization": ORGANIZATION,
        "website": WEBSITE,
        "repository": REPOSITORY,
        "license": {"name": LICENSE_NAME, "id": LICENSE_ID},
        "credits": CREDITS,
        "attribution": ATTRIBUTION,
        "notice": "Tout programme qui utilise ce logiciel doit rester public, gratuit et sous la même licence ; "
                  "l'usage dans un projet payant est interdit. Voir LICENSE.",
    }
