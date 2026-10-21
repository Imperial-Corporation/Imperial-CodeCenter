# Petites machines, disques durs et profil léger

Imperial CodeCenter tourne sur un vieux PC, un mini-PC, un Raspberry Pi 4/5 ou un petit VPS, même avec un disque dur à plateaux. La 1.0.4 apporte pour cela un **profil de ressources** et plusieurs optimisations du moteur. Rien n'est imposé : sans réglage, le comportement reste celui de la 1.0.3.

## Choisir un profil

`CODECENTER_PROFILE` (dans `.env`, ou `ICC_PROFILE` avec `compose.lite.yaml`) :

| Profil | Pour | Effet principal |
|---|---|---|
| `standard` (défaut) | machine confortable | aucun réglage imposé |
| `lite` | petit serveur, mini-PC, Raspberry Pi, HDD | 2 threads moteur, 4 connexions PostgreSQL, 20 ms de pause entre fichiers indexés, 32 Mio max de contenu lu par recherche, rescan toutes les heures, 2 recherches simultanées, priorité CPU réduite (`nice 10`) |
| `minimal` | ≤ 1,5 Gio de RAM, disque très lent | 1 thread, 2 connexions, 60 ms de pause, 16 Mio par recherche, rescan toutes les 2 h, 1 recherche à la fois, `nice 15` |
| `auto` | tu ne sais pas | choisit d'après la mémoire, les cœurs et le type de disque (HDD détecté via `/sys`) |

Les valeurs du profil ne sont que des **défauts** : une variable déjà définie (`CODECENTER_ENGINE_THREADS=3`, etc.) gagne toujours. Le profil actif est visible dans *Compte → Administration → Système*.

Avec Docker : `docker compose -f compose.yaml -f compose.lite.yaml up -d` (voir ci-dessous). Mesure d'abord, si tu veux : `python3 scripts/bench.py --url … --token …` donne un verdict et le profil conseillé.

## Ce qui rend la 1.0.4 légère

- **Index de contenu dans PostgreSQL** (migration `004`). Avant, chaque recherche relisait des centaines de fichiers sur le disque — fatal sur un HDD. Maintenant le contenu (en minuscules) est stocké une fois, avec un index `pg_trgm` : la recherche filtre en SQL, note par lots, et ne lit sur le disque que les quelques fichiers affichés (pour leurs extraits). Les résultats sont les mêmes qu'avant.
  - Dégradation gracieuse : migration absente, extension `pg_trgm` indisponible ou `CODECENTER_CONTENT_INDEX=off` → le moteur lit les fichiers comme en 1.0.3, sans erreur.
  - Coût : la base grossit à peu près de la taille du code indexé.
- **Redémarrage rapide** : les dates et tailles des fichiers sont conservées en base ; après un redémarrage, les fichiers inchangés ne sont **pas relus** (avant : tout l'arbre était relu, le pire cas sur un HDD).
- **Une seule lecture par fichier** (avant : lu deux fois quand il restait en place).
- **Pauses d'indexation** (`CODECENTER_INDEX_PAUSE_MS`, 0 à 5000) : l'indexation laisse respirer le disque et le processeur.
- **Priorités** : `CODECENTER_NICE` (CPU, 0–19) et `CODECENTER_IONICE=idle|low` (disque, Linux) — l'indexation ne gêne pas le reste de la machine.
- **Pool et threads** réglables : `CODECENTER_DB_MAX_CONNECTIONS` (défaut 10), `CODECENTER_ENGINE_THREADS`.
- **Cache de recherche** (`CODECENTER_SEARCH_CACHE`, défaut 128 entrées, 0 = off) : une requête répétée répond en ~2 ms ; il est vidé dès que l'index change.
- **Interface** : réponses compressées (gzip), fichiers statiques revalidés par ETag (réponse 304), pas de dépendance externe.
- **Exclusions** : n'indexe pas `node_modules`, `*.min.js`, etc. (voir [administration.md](administration.md)).

## PostgreSQL réglé pour peu de mémoire

`compose.lite.yaml` ajoute à la base : `max_connections=20`, `shared_buffers=64MB`, `work_mem=2MB`, `synchronous_commit=off`, `wal_compression=on`, `checkpoint_timeout=15min`, et des limites mémoire/CPU (`mem_limit` 384 Mio base, 640 Mio application). `synchronous_commit=off` regroupe les écritures (très utile sur HDD) : après une coupure brutale, au pire les dernières secondes d'indexation sont à refaire — l'index se reconstruit depuis les fichiers, la base reste cohérente.

Hors Docker, mets les mêmes valeurs dans `postgresql.conf`.

## Par où commencer sur un HDD

1. `CODECENTER_PROFILE=lite` (ou `minimal`).
2. Mets PostgreSQL et le dossier de données sur le même disque que le reste, mais **évite le rescan fréquent** : `CODECENTER_RESCAN_SECS=7200` (le watcher voit déjà les changements en direct).
3. Premier démarrage : l'indexation initiale peut durer ; la progression est visible dans *Administration → Index* et sur `/metrics`.
4. IA : sur une petite machine, utilise un fournisseur distant, ou un petit modèle local (Ollama avec un modèle de 1 à 3 milliards de paramètres) ; le mode « Approfondir » est plus gourmand.

## Limites connues

- Le mode « un seul fichier, sans PostgreSQL » (SQLite) n'existe pas : PostgreSQL reste nécessaire.
- Pas de recherche sémantique par embeddings (trop lourde pour ce profil) : la recherche est par mots-clés, enrichie par l'IA.
