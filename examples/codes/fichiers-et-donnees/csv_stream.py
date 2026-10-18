"""Lecture d'un CSV en flux : une ligne à la fois, mémoire constante."""

import csv
import io
from collections.abc import Iterator

SAMPLE = "nom,age\nAda,36\nLinus,54\nGrace,85\n"


def rows(handle) -> Iterator[dict[str, str]]:
    """Génère les lignes sans charger tout le fichier."""
    yield from csv.DictReader(handle)


def average_age(handle) -> float:
    total = count = 0
    for row in rows(handle):
        total += int(row["age"])
        count += 1
    return total / count if count else 0.0


if __name__ == "__main__":
    # Avec un vrai fichier : open(chemin, newline="", encoding="utf-8")
    print(average_age(io.StringIO(SAMPLE)))  # 58.333...
