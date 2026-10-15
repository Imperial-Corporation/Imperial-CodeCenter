# Variables d'environnement

Fichier : `config/.env` (développement) ou `/etc/imperial-codecenter/.env` (production). Modèle : `config/.env.exemple`.

| Variable | Défaut | Rôle |
|---|---|---|
| `DATABASE_URL` | — | PostgreSQL (obligatoire) |
| `CODECENTER_WATCH_PATHS` | — | dossiers surveillés, séparés par `:` |
| `CODECENTER_DATA_DIR` | `.local/data` | données de l'application |
| `CODECENTER_SECRET_KEY` / `_FILE` / `CODECENTER_KEY_DIR` | généré | clé Fernet |
| `CODECENTER_ADMIN_TOKEN` | vide | jeton Bearer des routes d'administration |
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
| `CODECENTER_FORCE_HTTPS` | 0 | HSTS et schéma HTTPS |
| `CODECENTER_RATE_LIMIT` | 240 | requêtes/min/IP (mode proxy) |
| `CODECENTER_CLOUDFLARE_IPS_FILE` | fichier fourni | plages Cloudflare |
