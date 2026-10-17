# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

import pytest

from icc_crawler.classify import DEFAULT_CATEGORY, RULES, classify


def test_default_when_nothing_matches():
    assert classify("src/foo.py", content="x = 1\n") == DEFAULT_CATEGORY == "divers"


def test_single_weak_hint_is_not_enough():
    assert classify("foo.py", description="a small web thing") == DEFAULT_CATEGORY
    assert classify("foo.py", content="import asyncio\n") == DEFAULT_CATEGORY


def test_path_keyword_is_strong():
    assert classify("src/api/users.py") == "api"
    assert classify("lib/sorting/quick.py") == "algorithmes"
    assert classify("frontend/components/button.js") == "web"


def test_topics_count_like_path_words():
    assert classify("src/main.py", topics=["concurrency"]) == "concurrence"
    assert classify("src/main.py", topics=["data-structures"]) == "algorithmes"


@pytest.mark.parametrize("content,expected", [
    ("import asyncio\nasync def f():\n    await asyncio.sleep(1)\n    # coroutine\n", "concurrence"),
    ("from flask import Flask\napp = Flask(__name__)\n@app.route('/')\ndef i(): ...\n", "api"),
    ("import sqlite3\nimport csv\n", "fichiers-et-donnees"),
    ("document.querySelector('a')\nel.addEventListener('click', f)\n", "web"),
    ("import subprocess\nimport argparse\n", "systeme"),
    ("FROM python:3.12\nRUN pip install x\n# image docker\n", "configuration"),
])
def test_content_signals(content, expected):
    assert classify("x.py", content=content) == expected


def test_description_words_are_weak_but_add_up():
    assert classify("foo.py", description="a small web thing") == DEFAULT_CATEGORY
    assert classify("foo.py", description="web frontend browser toolkit") == "web"


def test_python_from_import_is_not_dockerfile():
    # Régression : `from x import y` ne doit pas ressembler à une instruction FROM de Dockerfile.
    assert classify("x.py", content="from os import path\nfrom sys import argv\n") == DEFAULT_CATEGORY


def test_all_categories_are_valid_folder_names():
    for name in list(RULES) + [DEFAULT_CATEGORY]:
        assert name == name.lower() and "/" not in name and not name.startswith(".")


def test_very_large_content_is_sampled():
    content = "x = 1\n" * 100_000 + "import asyncio\nimport tokio\n"
    assert classify("x.py", content=content) == DEFAULT_CATEGORY   # signaux au-delà de 20 000 caractères ignorés
