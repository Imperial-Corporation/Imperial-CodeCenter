# Variables d'environnement

Fichier : `config/.env` (développement) ou `/etc/imperial-codecenter/.env` (production). Modèle : `config/.env.exemple`.

| Variable | Défaut | Rôle |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL (obligatoire) |
| `CODECENTER_WATCH_PATHS` | — | dossiers surveillés, séparés par `:` |
| `CODECENTER_DATA_DIR` | `.local/data` | données de l'application |
| `CODECENTER_SECRET_KEY` / `_FILE` / `CODECENTER_KEY_DIR` | généré | clé Fernet |
| `CODECENTER_ADMIN_TOKEN` | vide | jeton Bearer des routes d'administration |
| `CODECENTER_REGISTRATION` | open | `open` ou `closed` : inscriptions de comptes ([comptes.md](comptes.md)) |
| `CODECENTER_IMPORT_MAX_FILE_KB` | 1000 | taille max d'un fichier importé |
| `CODECENTER_IMPORT_MAX_UPLOAD_MB` | 50 | taille max d'un envoi |
| `CODECENTER_MIN_FREE_GB` | 2 | disque libre minimal |
| `CODECENTER_DEPOT_SECS` | 15 | période d'examen de `_depot` |
| `CODECENTER_MAX_FILE_BYTES` | 2000000 | taille max indexée |
| `CODECENTER_ORGANIZE` | on | `off` : indexer sans ranger |
| `CODECENTER_RESCAN_SECS` | 1800 | rescan complet (0 = non) |
| `CODECENTER_PROXY_MODE` | off | `off`, `nginx`, `cloudflare` |
| `CODECENTER_TRUSTED_PROXIES` | loopback | proxys de confiance |
| `CODECENTER_PUBLIC_HOST` / `CODECENTER_ALLOWED_HOSTS` | vide | hôtes acceptés |
| `CODECENTER_FORCE_HTTPS` | 0 | HSTS, schéma HTTPS et cookie de session `Secure` |
| `CODECENTER_RATE_LIMIT` | 240 | requêtes/min/IP (mode proxy) |
| `CODECENTER_CLOUDFLARE_IPS_FILE` | fichier fourni | plages Cloudflare |

## Nouveautés 1.0.4 (toutes facultatives)

| Variable | Défaut | Rôle |
|---|---|---|
| `CODECENTER_PROFILE` | standard | `standard`, `lite`, `minimal`, `auto` ([performance.md](performance.md)) ; ses valeurs ne remplacent jamais une variable déjà définie |
| `CODECENTER_ENGINE_THREADS` | auto | threads du moteur Rust (1–64) |
| `CODECENTER_DB_MAX_CONNECTIONS` | 10 | connexions PostgreSQL du moteur (1–100) |
| `CODECENTER_INDEX_PAUSE_MS` | 0 | pause entre deux fichiers indexés (0–5000 ms) |
| `CODECENTER_SEARCH_SCAN_MB` | 96 | contenu lu au plus par recherche |
| `CODECENTER_SEARCH_CONCURRENCY` | 4 | recherches IA simultanées |
| `CODECENTER_SEARCH_CACHE` | 128 | entrées du cache de recherche (0 = off) |
| `CODECENTER_CONTENT_INDEX` | on | `off` : pas de contenu en base (lecture des fichiers comme en 1.0.3) |
| `CODECENTER_NICE` | 0 | priorité CPU réduite (0–19) |
| `CODECENTER_IONICE` | — | `idle`, `low` ou `normal` (priorité disque, Linux) |
| `CODECENTER_EXCLUDE` / `CODECENTER_EXCLUDE_FILE` | — / `<données>/exclude.txt` | motifs d'exclusion de l'index ([administration.md](administration.md)) |
| `CODECENTER_BACKUP_EVERY_HOURS` / `_KEEP` / `_PASSPHRASE` / `_CODES` | 0 / 7 / — / 0 | sauvegardes planifiées ([sauvegardes.md](sauvegardes.md)) |
| `CODECENTER_METRICS_TOKEN` / `CODECENTER_METRICS_PUBLIC` | — / 0 | accès à `/metrics` |
| `NOM_FILE` | — | secrets lus dans un fichier (Docker) pour `DATABASE_URL`, `CODECENTER_ADMIN_TOKEN`, `CODECENTER_SECRET_KEY`, `CODECENTER_METRICS_TOKEN`, `CODECENTER_BACKUP_PASSPHRASE`, `GITHUB_TOKEN` |
| `ICC_PROFILE`, `ICC_VERSION`, `ICC_BIND`, `ICC_PORT` | — | variables du `compose` |
| `ICC_URL`, `ICC_TOKEN`, `ICC_BACKUP_PASSPHRASE` | — | CLI `icc` ([cli-mcp.md](cli-mcp.md)) et sauvegardes en ligne de commande |
