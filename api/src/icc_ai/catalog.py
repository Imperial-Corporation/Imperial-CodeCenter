# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.

"""Catalogue des fournisseurs pris en charge et réglages avancés.

Le catalogue est la source de vérité unique : le frontend l'interroge
pour construire le menu, les champs et les valeurs par défaut.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AdvancedField:
    """Description d'un réglage avancé (borné et typé)."""

    key: str
    label: str
    kind: str  # "int" ou "float"
    default: float
    minimum: float
    maximum: float
    help: str = ""


# Réglages avancés communs à tous les fournisseurs.
ADVANCED_FIELDS: tuple[AdvancedField, ...] = (
    AdvancedField(
        "timeout_s", "Délai d'attente (s)", "int", 60, 5, 600,
        "Durée maximale d'un appel au fournisseur.",
    ),
    AdvancedField(
        "max_output_tokens", "Tokens de sortie max", "int", 2048, 64, 32000,
        "Longueur maximale de la réponse générée.",
    ),
    AdvancedField(
        "context_window", "Fenêtre de contexte (tokens)", "int", 32000, 2048, 2_000_000,
        "Budget utilisé pour dimensionner le contexte envoyé au modèle.",
    ),
    AdvancedField(
        "temperature", "Température", "float", 0.2, 0, 2,
        "Créativité du modèle (ignorée par certains modèles).",
    ),
    AdvancedField(
        "max_tool_calls", "Appels d'outils max", "int", 6, 0, 20,
        "Nombre maximal d'appels d'outils par recherche (0 = désactivés).",
    ),
)


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    label: str
    kind: str  # "local" ou "remote"
    protocol: str  # openai | anthropic | gemini | ollama
    needs_api_key: bool
    default_base_url: str
    suggested_model: str
    accent: str  # couleur de l'icône (neutre, pas de logo de marque)
    glyph: str  # monogramme affiché dans l'icône
    default_context_window: int = 32000
    notes: str = ""
    extra_defaults: dict = field(default_factory=dict)


PROVIDERS: dict[str, ProviderSpec] = {
    spec.id: spec
    for spec in (
        ProviderSpec(
            "ollama", "Ollama", "local", "ollama", False,
            "http://127.0.0.1:11434", "llama3.1", "#5eead4", "Ol",
            default_context_window=8192,
            notes="API native d'Ollama. Le modèle doit gérer les outils pour les utiliser.",
        ),
        ProviderSpec(
            "omniroute", "OmniRoute", "local", "openai", False,
            "http://127.0.0.1:20128/v1", "", "#a78bfa", "Om",
            notes="Passerelle locale compatible OpenAI. Clé API facultative.",
        ),
        ProviderSpec(
            "claude", "Claude", "remote", "anthropic", True,
            "https://api.anthropic.com", "claude-sonnet-5-5", "#fb923c", "Cl",
            default_context_window=200000,
        ),
        ProviderSpec(
            "openai", "OpenAI", "remote", "openai", True,
            "https://api.openai.com/v1", "gpt-4o-mini", "#34d399", "Op",
            default_context_window=128000,
        ),
        ProviderSpec(
            "groq", "Groq", "remote", "openai", True,
            "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "#f472b6", "Gq",
            default_context_window=128000,
        ),
        ProviderSpec(
            "gemini", "Gemini", "remote", "gemini", True,
            "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash",
            "#60a5fa", "Ge",
            default_context_window=1_000_000,
        ),
        ProviderSpec(
            "openrouter", "OpenRouter", "remote", "openai", True,
            "https://openrouter.ai/api/v1", "anthropic/claude-sonnet-4.5", "#818cf8", "Or",
            default_context_window=128000,
            notes="Passerelle vers des centaines de modèles. Le nom du modèle est de la forme « éditeur/modèle ».",
        ),
        ProviderSpec(
            "mistral", "Mistral", "remote", "openai", True,
            "https://api.mistral.ai/v1", "mistral-large-latest", "#fbbf24", "Mi",
            default_context_window=128000,
        ),
        ProviderSpec(
            "deepseek", "DeepSeek", "remote", "openai", True,
            "https://api.deepseek.com/v1", "deepseek-chat", "#38bdf8", "Ds",
            default_context_window=64000,
        ),
        ProviderSpec(
            "xai", "xAI", "remote", "openai", True,
            "https://api.x.ai/v1", "grok-4", "#e5e7eb", "xA",
            default_context_window=128000,
        ),
        ProviderSpec(
            "together", "Together AI", "remote", "openai", True,
            "https://api.together.xyz/v1", "meta-llama/Llama-3.3-70B-Instruct-Turbo", "#f87171", "To",
            default_context_window=128000,
        ),
        ProviderSpec(
            "fireworks", "Fireworks AI", "remote", "openai", True,
            "https://api.fireworks.ai/inference/v1", "accounts/fireworks/models/llama-v3p3-70b-instruct",
            "#fb7185", "Fw",
            default_context_window=128000,
        ),
        ProviderSpec(
            "cerebras", "Cerebras", "remote", "openai", True,
            "https://api.cerebras.ai/v1", "llama-3.3-70b", "#a3e635", "Ce",
            default_context_window=64000,
        ),
        ProviderSpec(
            "nvidia", "NVIDIA NIM", "remote", "openai", True,
            "https://integrate.api.nvidia.com/v1", "meta/llama-3.3-70b-instruct", "#84cc16", "Nv",
            default_context_window=128000,
        ),
        ProviderSpec(
            "cohere", "Cohere", "remote", "openai", True,
            "https://api.cohere.ai/compatibility/v1", "command-r-plus", "#2dd4bf", "Co",
            default_context_window=128000,
            notes="API de compatibilité OpenAI de Cohere.",
        ),
        ProviderSpec(
            "lmstudio", "LM Studio", "local", "openai", False,
            "http://127.0.0.1:1234/v1", "", "#c084fc", "LM",
            default_context_window=8192,
            notes="Serveur local de LM Studio. Charge un modèle, puis démarre le serveur.",
        ),
        ProviderSpec(
            "llamacpp", "llama.cpp / vLLM", "local", "openai", False,
            "http://127.0.0.1:8080/v1", "", "#fcd34d", "Ll",
            default_context_window=8192,
            notes="Serveur compatible OpenAI (llama-server, vLLM, LocalAI…). Clé API facultative.",
        ),
        ProviderSpec(
            "custom", "Compatible OpenAI (autre)", "remote", "openai", False,
            "https://", "", "#94a3b8", "Cx",
            notes="Tout service compatible OpenAI : renseigne son URL, son modèle et, si besoin, sa clé.",
        ),
    )
}


def get_spec(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        raise KeyError(f"Fournisseur inconnu : {provider_id}") from None


def default_advanced(provider_id: str) -> dict:
    """Valeurs par défaut des réglages avancés pour un fournisseur."""
    spec = get_spec(provider_id)
    values = {f.key: f.default for f in ADVANCED_FIELDS}
    values["context_window"] = spec.default_context_window
    values.update(spec.extra_defaults)
    return {k: (int(v) if isinstance(v, float) and v.is_integer() and k != "temperature" else v)
            for k, v in values.items()}


def catalog_payload() -> dict:
    """Catalogue sérialisable pour le frontend (aucun secret)."""
    return {
        "providers": [
            {
                "id": s.id,
                "label": s.label,
                "kind": s.kind,
                "needs_api_key": s.needs_api_key,
                "default_base_url": s.default_base_url,
                "suggested_model": s.suggested_model,
                "accent": s.accent,
                "glyph": s.glyph,
                "notes": s.notes,
                "advanced_defaults": default_advanced(s.id),
            }
            for s in PROVIDERS.values()
        ],
        "advanced_fields": [
            {
                "key": f.key, "label": f.label, "kind": f.kind,
                "minimum": f.minimum, "maximum": f.maximum, "help": f.help,
            }
            for f in ADVANCED_FIELDS
        ],
    }
