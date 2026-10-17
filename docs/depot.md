# Ajouter des scripts (zone de dépôt du serveur)

Depuis la 1.0.1, l'interface web n'a plus de section « Ajouter des scripts » : les scripts s'ajoutent côté serveur.

## Zone de dépôt

Copie tes fichiers dans `<dossier surveillé>/_depot/` : ils sont examinés toutes les 15 s (`CODECENTER_DEPOT_SECS`), rangés dans `<dossier surveillé>/tag1/tag2/<langage>/fichier`, puis indexés.

Pour étiqueter, au choix :

1. un fichier `tags.txt` dans `_depot/` (`reseau, debug`) : s'applique à tout ce qui est déposé ;
2. un fichier `monscript.py.tags` à côté du script ;
3. des sous-dossiers : `_depot/reseau/ping.py` reçoit le tag `reseau`.

Sans tag, classement automatique par contenu. Un registre (`depot-ledger.json`) évite de ranger deux fois le même fichier ; un fichier en cours de copie est attendu ; les écritures sont atomiques. Un import est refusé sous `CODECENTER_MIN_FREE_GB` d'espace libre.

## Routes d'administration (toujours disponibles)

Pour les scripts et outils : `POST /api/v1/library/import` (multipart `files`, `tags`, `auto_classify`, `keep_folders`), `POST /api/v1/library/depot/scan`, `GET /api/v1/library/info` — avec `CODECENTER_ADMIN_TOKEN` si défini ([api.md](api.md)). Limites : 1 Mo par fichier, 50 Mo par envoi (`CODECENTER_IMPORT_*`) ; ZIP : chemins dangereux, liens symboliques et archives piégées rejetés.

Le crawler GitHub ([crawler.md](crawler.md)) alimente la bibliothèque automatiquement.
