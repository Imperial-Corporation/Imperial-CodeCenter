# Référence de l'API (v1.0.0)

Documentation interactive : `/docs`. Erreurs : `{"detail": "...", "code": "..."}`.

## Système
`GET /api/health` · `GET /api/v1/status` · `GET /api/v1/stats` · `GET /api/v1/proxy/check` · `WS /ws/status`

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
