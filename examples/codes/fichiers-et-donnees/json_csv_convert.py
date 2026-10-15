"""Convertit un fichier CSV en JSON (et inversement) avec la bibliothèque standard.

Usage : python json_csv_convert.py donnees.csv sortie.json
"""

import csv
import json
import sys
from pathlib import Path


def csv_to_json(source: Path, destination: Path) -> int:
    with source.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    destination.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(rows)


def json_to_csv(source: Path, destination: Path) -> int:
    rows = json.loads(source.read_text(encoding="utf-8"))
    if not rows:
        destination.write_text("", encoding="utf-8")
        return 0
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    convert = csv_to_json if src.suffix.lower() == ".csv" else json_to_csv
    print(f"{convert(src, dst)} lignes converties")
