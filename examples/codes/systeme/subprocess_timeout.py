"""Lancer une commande externe avec délai maximal, sans passer par le shell."""

import subprocess


def run(command: list[str], timeout: float = 10.0) -> tuple[int, str]:
    """Retourne (code de sortie, sortie standard). `command` est une liste : pas d'injection shell."""
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return 124, f"Délai de {timeout} s dépassé"
    except FileNotFoundError:
        return 127, f"Commande introuvable : {command[0]}"
    return result.returncode, result.stdout.strip() or result.stderr.strip()


if __name__ == "__main__":
    print(run(["echo", "bonjour"]))
    print(run(["sleep", "5"], timeout=0.5))
