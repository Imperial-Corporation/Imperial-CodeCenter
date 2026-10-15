# Installation

Prérequis : Linux (systemd), accès `sudo`, Python ≥ 3.10, Rust (`cargo`), [`uv`](https://docs.astral.sh/uv/), PostgreSQL.

## Tout en une commande

```bash
./scripts/install.sh
```

`install.sh` enchaîne : préparation du serveur (`deploy.sh`), compilation et installation (`release.sh`), scripts d'exemple, permissions (`fix_permissions.sh`), zone de dépôt `_depot`, collecteur GitHub, proxy (optionnel) et diagnostic final (`doctor.sh`). Il est relançable : une mise à jour conserve la base, la clé de chiffrement, tes fichiers et ta configuration.

Options :

| Option | Effet |
|---|---|
| `--skip-system` | ne relance pas `deploy.sh` (serveur déjà prêt) |
| `--force-examples` | réinstalle les exemples supprimés |
| `--generate-admin-token` | crée `CODECENTER_ADMIN_TOKEN` (affiché une seule fois) |
| `--proxy nginx\|cloudflare --server-name NOM` | active l'intégration proxy ([proxy.md](proxy.md)) |
| `--tls-cert` / `--tls-key` | HTTPS sur nginx |
| `--no-doctor` | saute le diagnostic |

## Développement

```bash
cp config/.env.exemple config/.env     # DATABASE_URL, CODECENTER_WATCH_PATHS
./scripts/build.sh                      # dépendances, moteur Rust, API sur http://127.0.0.1:8000
```

## Diagnostic

```bash
./scripts/doctor.sh      # lecture seule : service, permissions, journal, base, API, disque
```

Dans l'interface, `GET /api/v1/index/health` donne les fichiers en échec, les dossiers non inscriptibles et l'espace disque.

## Mise à jour / scripts

| Script | Rôle |
|---|---|
| `deploy.sh` | compte de service, base, dépendances système (une fois) |
| `release.sh` | construit et installe une version, redémarre le service |
| `fix_permissions.sh` | répare les permissions du dossier surveillé |
| `install_examples.sh` | copie les exemples (une seule fois, sauf `--force`) |
| `setup_proxy.sh` | active/désactive nginx / Cloudflare |
| `update_cloudflare_ips.sh` | met à jour les plages Cloudflare |
| `doctor.sh` | diagnostic |
| `clean.sh` | nettoie les artefacts de build |

Ne supprime jamais `CODECENTER_SECRET_KEY` de `/etc/imperial-codecenter/.env` : les clés API enregistrées deviendraient illisibles.
