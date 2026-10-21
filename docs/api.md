# Référence de l'API (v1.0.4)

Documentation interactive : `/docs`. Erreurs : `{"detail": "...", "code": "..."}`.

## Système
`GET /api/health` · `GET /api/v1/status` · `GET /api/v1/stats` · `GET /api/v1/proxy/check` · `WS /ws/status`

## Identité du projet
`GET /api/v1/about` (nom, version, organisation, site, licence, crédits) · `GET /LICENSE` · `GET /NOTICE`

## Comptes et discussions enregistrées
Cookie de session `icc_session` (HttpOnly) ; refus inter-sites (en-tête `Origin`).

| Route | Corps | Rôle |
|---|---|---|
| `GET /api/v1/auth/me` | — | `{enabled, registration_open, user}` |
| `POST /api/v1/auth/register` · `/auth/login` | `{username, password}` | crée / ouvre la session ; 429 après trop d'échecs |
| `POST /api/v1/auth/logout` | — | ferme la session |
| `POST /api/v1/auth/password` | `{current, new}` | change le mot de passe |
| `POST /api/v1/auth/delete` | `{password}` | supprime le compte et ses discussions |
| `GET /api/v1/chats` | — | liste des discussions enregistrées |
| `POST /api/v1/chats` | `{conversation_id, title?}` | rend la discussion en cours définitive |
| `POST /api/v1/chats/{id}/open` | — | rouvre : `{id, title, conversation_id, turns}` |
| `PATCH /api/v1/chats/{id}` | `{title}` | renomme |
| `DELETE /api/v1/chats/{id}` | — | supprime |

Une discussion enregistrée reçoit automatiquement les tours suivants de `/chat`. Voir [comptes.md](comptes.md).

## Recherche et chat
| Route | Corps | Résultat |
|---|---|---|
| `POST /api/v1/search` | `{query, limit?, language?, category?, depth?}` | réponse structurée + `meta.conversation_id` |
| `POST /api/v1/chat` | `{conversation_id, message, depth?, language?, category?}` | réponse de suite (mêmes champs) |

`answer.follow_ups` : pistes (max 4). Statuts : `ok`, `search_only`, `no_results`. Erreurs de chat : 404 conversation inconnue/expirée, 409 occupée, 413 trop longue.

## Configuration IA
`GET /api/v1/ai/catalog` · `GET /api/v1/ai/config` · `PUT /api/v1/ai/providers/{id}` · `POST /api/v1/ai/providers/{id}/test`

## Administration (jeton si `CODECENTER_ADMIN_TOKEN`, refus inter-sites)
| Route | Rôle |
|---|---|
| `GET /api/v1/library/info` | dossier, dépôt, limites, extensions |
| `POST /api/v1/library/import` | multipart `files`, `tags`, `auto_classify`, `keep_folders` |
| `POST /api/v1/library/depot/scan` | range la zone de dépôt maintenant |
| `GET /api/v1/index/health` | échecs, dossiers non inscriptibles, ignorés, disque |
| `GET /api/v1/crawler` | état, historique, disque |
| `POST /api/v1/crawler/start` | `{languages, query, start_at, stop_at, max_minutes, max_gb, max_library_gb, min_free_gb, …}` |
| `POST /api/v1/crawler/stop` | arrête ou annule la planification |
| `PUT /api/v1/crawler/token` | `{token}` ou `{clear: true}` |

Codes : 401 jeton requis, 403 requête inter-sites, 409 travail déjà actif, 413 trop volumineux, 422 données invalides, 503 service/permissions.

## Ajouts 1.0.4

**Accès direct au code** (compte ou jeton d'API `Authorization: Bearer icc_pat_…`, sans IA) :

| Route | Rôle |
|---|---|
| `GET /api/v1/code/search?q=&limit=&language=&category=` | recherche par mots-clés, extraits et lignes |
| `GET /api/v1/code/documents/{id}?start_line=&end_line=` | contenu d'un document |
| `GET /api/v1/code/filters` | langages et catégories |

**Jetons d'API** (session uniquement) : `GET/POST /api/v1/auth/tokens` (`{name, expires_days}` ; le jeton n'est renvoyé qu'à la création), `DELETE /api/v1/auth/tokens/{id}`. `GET /api/v1/auth/me` ajoute `is_admin` pour un utilisateur connecté.

**Administration** (administrateur ; cookie de session ou `CODECENTER_ADMIN_TOKEN`) :

| Route | Rôle |
|---|---|
| `GET /api/v1/admin/users` | comptes + état des inscriptions |
| `PUT /api/v1/admin/registration` | `{open}` |
| `POST /api/v1/admin/users/{id}/disabled` | `{disabled}` |
| `POST /api/v1/admin/users/{id}/password` | `{password}` (efface les clés IA du compte) |
| `DELETE /api/v1/admin/users/{id}` | supprime le compte |
| `GET /api/v1/admin/index` · `POST /api/v1/admin/index/rescan` | progression du passage, statistiques · relance |
| `GET/PUT /api/v1/admin/index/exclusions` | `{patterns: [...]}` (un passage suit l'enregistrement) |
| `GET /api/v1/admin/system` | ressources du serveur |
| `GET/POST /api/v1/admin/backups` · `GET/DELETE /api/v1/admin/backups/{nom}` | sauvegardes |
| `GET /metrics` | Prometheus ([administration.md](administration.md)) |
