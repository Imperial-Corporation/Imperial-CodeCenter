"""Hachage de mot de passe avec scrypt (bibliothèque standard) et sel aléatoire."""

import hashlib
import hmac
import os


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    salt_hex, digest_hex = stored.split("$")
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1
    )
    # compare_digest évite les attaques par mesure du temps de comparaison
    return hmac.compare_digest(digest.hex(), digest_hex)


if __name__ == "__main__":
    stored = hash_password("correct horse battery staple")
    print(verify_password("correct horse battery staple", stored))  # True
    print(verify_password("mauvais", stored))  # False
