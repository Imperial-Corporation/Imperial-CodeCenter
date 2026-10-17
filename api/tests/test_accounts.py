# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Comptes : mots de passe, sessions, anti force brute, discussions enregistrées."""

import json
import sqlite3

import pytest

from icc_ai import accounts as acc
from icc_ai.accounts import AccountError, AccountStore, hash_password, verify_password


class Clock:
    def __init__(self):
        self.now = 1_000_000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    s = AccountStore(tmp_path / "accounts.db", clock=clock)
    yield s
    s.close()


def code_of(excinfo):
    return excinfo.value.code


def test_empreinte_salee_et_verification():
    a, b = hash_password("motdepasse-1"), hash_password("motdepasse-1")
    assert a != b and a.startswith("scrypt$") and "motdepasse" not in a
    assert verify_password("motdepasse-1", a) and not verify_password("autre", a)
    assert not verify_password("x", "n'importe quoi") and not verify_password("x", "md5$1$2$3$4$5")


@pytest.mark.parametrize("name", ["ab", "a" * 33, "avec espace", "é/\\", "", "<script>"])
def test_pseudo_invalide(store, name):
    with pytest.raises(AccountError) as e:
        store.register(name, "motdepasse-ok")
    assert code_of(e) == "invalid_username"


def test_mot_de_passe_invalide(store):
    for password, code in [("court", "weak_password"), ("x" * 129, "invalid_password"), ("Alice-user", "weak_password")]:
        with pytest.raises(AccountError) as e:
            store.register("Alice-user", password)
        assert code_of(e) == code


def test_inscription_connexion_et_pseudo_unique_sans_casse(store):
    user = store.register("Alice", "motdepasse-ok")
    with pytest.raises(AccountError) as e:
        store.register("alice", "autre-motdepasse")
    assert code_of(e) == "username_taken" and e.value.status == 409
    assert store.authenticate("ALICE", "motdepasse-ok")["id"] == user["id"]
    assert store.user_count() == 1


def test_mauvais_identifiants_message_uniforme(store):
    store.register("alice", "motdepasse-ok")
    messages = set()
    for name, password in [("alice", "faux-faux-faux"), ("inconnu", "motdepasse-ok")]:
        with pytest.raises(AccountError) as e:
            store.authenticate(name, password, "1.2.3.4")
        assert e.value.status == 401
        messages.add(e.value.message)
    assert len(messages) == 1


def test_force_brute_blocage_puis_deblocage(store, clock):
    store.register("alice", "motdepasse-ok")
    for _ in range(acc.MAX_FAILURES):
        with pytest.raises(AccountError):
            store.authenticate("alice", "faux-faux-faux", "9.9.9.9")
    with pytest.raises(AccountError) as e:  # même le bon mot de passe est refusé
        store.authenticate("alice", "motdepasse-ok", "9.9.9.9")
    assert e.value.status == 429 and e.value.retry_after > 0
    assert store.authenticate("alice", "motdepasse-ok", "8.8.8.8")  # autre IP : pas bloquée
    clock.now += acc.FAILURE_WINDOW_S + 1
    assert store.authenticate("alice", "motdepasse-ok", "9.9.9.9")


def test_succes_remet_les_echecs_a_zero(store):
    store.register("alice", "motdepasse-ok")
    for _ in range(acc.MAX_FAILURES - 1):
        with pytest.raises(AccountError):
            store.authenticate("alice", "faux-faux-faux", "1.1.1.1")
    store.authenticate("alice", "motdepasse-ok", "1.1.1.1")
    for _ in range(acc.MAX_FAILURES - 1):
        with pytest.raises(AccountError):
            store.authenticate("alice", "faux-faux-faux", "1.1.1.1")


def test_inscriptions_fermees(tmp_path):
    s = AccountStore(tmp_path / "c.db", registration_open=False)
    with pytest.raises(AccountError) as e:
        s.register("alice", "motdepasse-ok")
    assert code_of(e) == "registration_closed" and e.value.status == 403
    s.close()


def test_sessions_expiration_et_fin(store, clock):
    user = store.register("alice", "motdepasse-ok")
    token = store.create_session(user["id"])
    assert store.user_for_token(token)["username"] == "alice"
    assert store.user_for_token(None) is None and store.user_for_token("faux") is None
    assert store.user_for_token("x" * 500) is None
    clock.now += acc.SESSION_TTL_S + 1
    assert store.user_for_token(token) is None
    token2 = store.create_session(user["id"])
    store.end_session(token2)
    assert store.user_for_token(token2) is None


def test_jeton_stocke_sous_forme_d_empreinte(store, tmp_path):
    user = store.register("alice", "motdepasse-ok")
    token = store.create_session(user["id"])
    db = sqlite3.connect(tmp_path / "accounts.db")
    stored = [r[0] for r in db.execute("SELECT token_hash FROM sessions")]
    db.close()
    assert token not in stored and len(stored[0]) == 64
    assert oct((tmp_path / "accounts.db").stat().st_mode & 0o777) == "0o600"


def test_changement_de_mot_de_passe_coupe_les_autres_sessions(store):
    user = store.register("alice", "motdepasse-ok")
    mine, other = store.create_session(user["id"]), store.create_session(user["id"])
    with pytest.raises(AccountError):
        store.change_password(user["id"], "mauvais-actuel", "nouveau-motdepasse", mine)
    store.change_password(user["id"], "motdepasse-ok", "nouveau-motdepasse", mine)
    assert store.user_for_token(mine) and store.user_for_token(other) is None
    store.authenticate("alice", "nouveau-motdepasse")
    with pytest.raises(AccountError):
        store.authenticate("alice", "motdepasse-ok")


def test_suppression_du_compte_efface_tout(store):
    user = store.register("alice", "motdepasse-ok")
    token = store.create_session(user["id"])
    store.save_chat(user["id"], "t", [{"question": "q", "response": {}}])
    with pytest.raises(AccountError):
        store.delete_account(user["id"], "faux-faux-faux")
    store.delete_account(user["id"], "motdepasse-ok")
    assert store.user_for_token(token) is None and store.user_count() == 0
    assert store.list_chats(user["id"]) == []


def turns(n=1):
    return [{"question": f"q{i}", "response": {"answer": {"summary": f"r{i}"}}} for i in range(n)]


def test_discussions_cycle_complet_et_isolation(store):
    alice, bob = store.register("alice", "motdepasse-ok"), store.register("bob", "motdepasse-ok")
    saved = store.save_chat(alice["id"], "Ma question", turns(2))
    assert saved["turn_count"] == 2
    assert store.list_chats(bob["id"]) == []
    for call in (lambda: store.get_chat(bob["id"], saved["id"]), lambda: store.delete_chat(bob["id"], saved["id"]),
                 lambda: store.rename_chat(bob["id"], saved["id"], "x"),
                 lambda: store.append_turn(bob["id"], saved["id"], {})):
        with pytest.raises(AccountError) as e:
            call()
        assert code_of(e) == "chat_not_found"
    store.append_turn(alice["id"], saved["id"], turns(1)[0])
    got = store.get_chat(alice["id"], saved["id"])
    assert len(got["turns"]) == 3 and store.list_chats(alice["id"])[0]["turn_count"] == 3
    assert store.rename_chat(alice["id"], saved["id"], "  Nouveau   titre ")["title"] == "Nouveau titre"
    store.delete_chat(alice["id"], saved["id"])
    assert store.list_chats(alice["id"]) == []


def test_titre_tronque_et_defaut(store):
    user = store.register("alice", "motdepasse-ok")
    long = store.save_chat(user["id"], "x" * 500, turns())
    assert len(long["title"]) == acc.MAX_TITLE_CHARS and long["title"].endswith("…")
    assert store.save_chat(user["id"], "   ", turns())["title"] == "Discussion"


def test_limites_de_discussions(store, monkeypatch):
    user = store.register("alice", "motdepasse-ok")
    monkeypatch.setattr(acc, "MAX_CHATS_PER_USER", 2)
    store.save_chat(user["id"], "a", turns())
    store.save_chat(user["id"], "b", turns())
    with pytest.raises(AccountError) as e:
        store.save_chat(user["id"], "c", turns())
    assert code_of(e) == "too_many_chats"
    monkeypatch.setattr(acc, "MAX_CHAT_BYTES", 50)
    with pytest.raises(AccountError) as e:
        store.save_chat(user["id"], "d", turns(3))
    assert code_of(e) in {"chat_too_large", "too_many_chats"}


def test_ordre_recent_d_abord(store, clock):
    user = store.register("alice", "motdepasse-ok")
    first = store.save_chat(user["id"], "vieux", turns())
    clock.now += 10
    second = store.save_chat(user["id"], "récent", turns())
    assert [c["id"] for c in store.list_chats(user["id"])] == [second["id"], first["id"]]
    clock.now += 10
    store.append_turn(user["id"], first["id"], turns()[0])
    assert store.list_chats(user["id"])[0]["id"] == first["id"]


def test_sqlite_injection_inoffensive(store):
    store.register("alice", "motdepasse-ok")
    with pytest.raises(AccountError):
        store.authenticate("alice' OR '1'='1", "x", "")
    assert json.dumps(store.list_chats("' OR 1=1 --")) == "[]"
