"""Configuration globale : chiffrement, persistance, validation."""

import json
import os
import stat

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from conftest import make_config
from icc_ai.catalog import PROVIDERS, default_advanced
from icc_ai.config.crypto import SecretBox
from icc_ai.config.models import AdvancedSettings, ProviderConfigIn, validate_base_url
from icc_ai.config.store import ConfigStore
from icc_ai.errors import ConfigError

KEY = "sk-secret-123456"


def test_fournisseurs_attendus():
    assert set(PROVIDERS) == {"ollama", "omniroute", "claude", "openai", "groq", "gemini"}
    assert PROVIDERS["ollama"].kind == "local" and PROVIDERS["claude"].kind == "remote"
    assert not PROVIDERS["ollama"].needs_api_key and PROVIDERS["openai"].needs_api_key


def test_cle_chiffree_au_repos(store, tmp_path):
    store.save("openai", make_config(base_url="https://api.openai.com/v1", model="gpt-4o-mini", api_key=KEY))
    raw = (tmp_path / "data" / "ai" / "providers.json").read_text()
    assert KEY not in raw
    assert "fernet:v1:" in raw
    assert store.resolve("openai").api_key == KEY


def test_permissions_strictes(store, tmp_path):
    store.save("openai", make_config(base_url="https://api.openai.com/v1", model="m", api_key=KEY))
    ai_dir = tmp_path / "data" / "ai"
    assert stat.S_IMODE(os.stat(ai_dir).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(ai_dir / "providers.json").st_mode) == 0o600


def test_vue_publique_sans_secret(store):
    store.save("claude", make_config(base_url="https://api.anthropic.com", model="claude-x", api_key=KEY))
    view = store.public_view()
    assert KEY not in json.dumps(view)
    assert view["providers"]["claude"]["api_key_set"] is True
    assert view["providers"]["openai"]["api_key_set"] is False
    assert view["providers"]["openai"]["configured"] is False


def test_un_seul_fournisseur_actif_et_configs_conservees(store):
    store.save("ollama", make_config(model="llama3.1"))
    store.save("openai", make_config(base_url="https://api.openai.com/v1", model="gpt-4o-mini", api_key=KEY))
    view = store.public_view()
    assert view["active"] == "openai"
    # La configuration d'Ollama est conservée malgré le changement.
    assert view["providers"]["ollama"]["model"] == "llama3.1"
    assert store.active_runtime().provider_id == "openai"


def test_enregistrer_sans_activer_garde_l_actif(store):
    store.save("ollama", make_config())
    store.save("groq", make_config(base_url="https://api.groq.com/openai/v1", model="m",
                                   api_key=KEY, activate=False))
    assert store.public_view()["active"] == "ollama"


def test_cle_conservee_si_absente_et_effacee_sur_demande(store):
    base = dict(base_url="https://api.openai.com/v1", model="m")
    store.save("openai", make_config(api_key=KEY, **base))
    store.save("openai", make_config(**base))  # api_key=None : on garde
    assert store.resolve("openai").api_key == KEY
    store.save("openai", make_config(clear_api_key=True, **base))
    assert store.resolve("openai").api_key is None


def test_resolve_brouillon_reutilise_la_cle_enregistree(store):
    store.save("openai", make_config(base_url="https://api.openai.com/v1", model="m", api_key=KEY))
    draft = make_config(base_url="https://api.openai.com/v1", model="autre")
    runtime = store.resolve("openai", draft)
    assert runtime.model == "autre" and runtime.api_key == KEY
    other = make_config(base_url="https://api.openai.com/v1", model="autre", api_key="sk-nouvelle")
    assert store.resolve("openai", other).api_key == "sk-nouvelle"


def test_repr_ne_montre_pas_la_cle(store):
    store.save("openai", make_config(base_url="https://api.openai.com/v1", model="m", api_key=KEY))
    assert KEY not in repr(store.resolve("openai"))


def test_aucun_actif_par_defaut(store):
    assert store.active_runtime() is None


def test_fournisseur_inconnu(store):
    with pytest.raises(KeyError):
        store.save("inconnu", make_config())


def test_cle_de_chiffrement_changee(store, tmp_path):
    store.save("openai", make_config(base_url="https://api.openai.com/v1", model="m", api_key=KEY))
    other = ConfigStore(tmp_path / "data", SecretBox(Fernet.generate_key()))
    with pytest.raises(ConfigError, match="Saisis de nouveau"):
        other.resolve("openai")


def test_fichier_corrompu(store, tmp_path):
    (tmp_path / "data" / "ai").mkdir(parents=True)
    (tmp_path / "data" / "ai" / "providers.json").write_text("{pas du json")
    with pytest.raises(ConfigError):
        store.public_view()


def test_cle_depuis_environnement(monkeypatch, tmp_path):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("CODECENTER_SECRET_KEY", key)
    box = SecretBox.from_environment(tmp_path)
    assert box.decrypt(box.encrypt("x")) == "x"
    assert not list(tmp_path.iterdir())  # aucune clé écrite à côté


def test_cle_generee_en_dev_avec_permissions(monkeypatch, tmp_path):
    monkeypatch.delenv("CODECENTER_SECRET_KEY", raising=False)
    monkeypatch.delenv("CODECENTER_SECRET_KEY_FILE", raising=False)
    monkeypatch.delenv("CODECENTER_KEY_DIR", raising=False)
    SecretBox.from_environment(tmp_path / "keys")
    key_file = tmp_path / "keys" / "secret.key"
    assert key_file.is_file() and stat.S_IMODE(os.stat(key_file).st_mode) == 0o600
    # Une seconde instance relit la même clé.
    a = SecretBox.from_environment(tmp_path / "keys")
    assert a.decrypt(SecretBox(key_file.read_bytes().strip()).encrypt("y")) == "y"


def test_cle_invalide():
    with pytest.raises(ConfigError):
        SecretBox(b"pas-une-cle")


@pytest.mark.parametrize("url", [
    "ftp://x", "http://", "http://user:pw@host", "http://h/?a=1", "http://h/#f", "javascript:alert(1)",
])
def test_url_invalide(url):
    with pytest.raises(ValueError):
        validate_base_url(url)


def test_url_normalisee():
    assert validate_base_url(" http://127.0.0.1:11434/ ") == "http://127.0.0.1:11434"


def test_modele_et_cle_valides():
    with pytest.raises(ValidationError):
        ProviderConfigIn(base_url="http://h", model="a b; rm -rf")
    with pytest.raises(ValidationError):
        ProviderConfigIn(base_url="http://h", model="m", api_key="a b")
    with pytest.raises(ValidationError):
        ProviderConfigIn(base_url="http://h", model="m", inconnu=1)


def test_reglages_avances_bornes():
    assert AdvancedSettings.for_provider("claude").context_window == 200000
    with pytest.raises(ValueError):
        AdvancedSettings.for_provider("claude", {"timeout_s": 1})
    with pytest.raises(ValueError):
        AdvancedSettings.for_provider("claude", {"max_tool_calls": 999})
    with pytest.raises(Exception):
        AdvancedSettings.for_provider("claude", {"inconnu": 1})
    assert default_advanced("ollama")["context_window"] == 8192
