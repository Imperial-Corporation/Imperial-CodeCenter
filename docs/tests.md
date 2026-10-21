# Tests

```bash
cd api     && uv sync && uv run pytest      # ≈ 690 tests (les tests d'intégration sont ignorés sans base)
cd crawler && uv run pytest                  # 256 tests
cd engine  && cargo test                     # 33 tests
```

Intégration réelle (moteur + PostgreSQL de test, la table `code_examples` est vidée) :

```bash
cd engine && uv run --with maturin maturin develop --manifest-path Cargo.toml --python ../api/.venv/bin/python
cd ../api && ICC_TEST_DATABASE_URL=postgresql://user@127.0.0.1:5432/base_test uv run pytest tests/test_integration_engine.py
```

Les fournisseurs IA et GitHub sont simulés : aucun test n'appelle un vrai service. Vérifications faites en plus à la main pendant le développement : parcours complet dans un navigateur (Playwright) avec un faux fournisseur : discussion, comptes, enregistrement, réouverture, barre latérale, mobile, scénario de permissions (service non-root, dossier `root:root 775`), `nginx -t`.

## Tests de bout en bout (interface)

Neuf scénarios Playwright (Chromium) vérifient l'interface réelle avec une API simulée : connexion, recherche,
bascule FR/EN, panneau d'administration, jetons. Ils s'exécutent dans la CI (job « e2e »).

```bash
cd e2e && pip install playwright pytest && playwright install chromium
PYTHONPATH=../api/src python -m pytest .
```
