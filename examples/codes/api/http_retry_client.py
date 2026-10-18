"""Client HTTP avec timeout et nouvelles tentatives (backoff exponentiel)."""

import time
import urllib.error
import urllib.request


def fetch(url: str, retries: int = 3, timeout: float = 5.0) -> bytes:
    """Récupère `url` ; réessaie sur erreur réseau ou réponse 5xx."""
    delay = 0.5
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code < 500 or attempt == retries:
                raise  # erreur client : inutile de réessayer
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries:
                raise
        time.sleep(delay)
        delay *= 2  # 0.5 s, 1 s, 2 s...
    raise RuntimeError("inatteignable")


if __name__ == "__main__":
    try:
        print(len(fetch("http://127.0.0.1:9/", retries=2, timeout=1)))
    except OSError as error:
        print("échec après tentatives :", error)
