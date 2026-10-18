# Déploiement avec Docker

Une seule image (moteur Rust + API + interface) et un PostgreSQL : `compose.yaml` lance les deux. Rien à installer sur la machine hôte à part Docker (Compose v2.24 ou plus récent).

## Démarrage rapide

```bash
git clone https://github.com/Imperial-Corporation/Imperial-CodeCenter
cd Imperial-CodeCenter
cp docker/env.exemple .env
# choisis un mot de passe de base (lettres et chiffres) :
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
docker compose up -d
```

Ouvre <http://127.0.0.1:8000>, **crée ton compte en premier** : le premier compte est l'administrateur (bibliothèque, crawler). Puis règle ton fournisseur d'IA dans l'engrenage.

Sans cloner le dépôt, tu peux aussi récupérer seulement `compose.yaml` et `.env` : l'image est téléchargée depuis `ghcr.io/imperial-corporation/imperial-codecenter`.

## Ce que fait le conteneur au démarrage

1. crée une clé de chiffrement dans le volume `/data` (une seule fois, droits `0600`) ;
2. attend PostgreSQL, puis applique les migrations SQL **une seule fois chacune** (table `codecenter_schema_migrations`, comme `scripts/release.sh`) ;
3. copie les scripts d'exemple dans `/data/codes` (une seule fois : ceux que tu supprimes ne reviennent pas) ;
4. lance l'API (`tini` gère les signaux, donc le crawler s'arrête proprement).

## Données et sauvegardes

| Volume | Contenu |
|---|---|
| `icc-data` → `/data` | bibliothèque de code (`codes/`), comptes et clés chiffrées (SQLite), clé serveur, imports |
| `icc-db` | base PostgreSQL |

```bash
# sauvegarde de la base
docker compose exec -T db pg_dump -U codecenter imperial_codecenter > sauvegarde.sql
# sauvegarde des données
docker run --rm -v imperial-codecenter_icc-data:/data -v "$PWD":/out busybox tar czf /out/icc-data.tgz -C /data .
```

Les clés API des comptes sont chiffrées par le mot de passe de chaque compte : une sauvegarde ne les expose pas, mais un mot de passe oublié les rend illisibles.

## Réglages

Tout passe par `.env` (modèle : `docker/env.exemple`). Les variables `CODECENTER_*` de [configuration.md](configuration.md) sont transmises au conteneur telles quelles.

| Variable | Rôle |
|---|---|
| `POSTGRES_PASSWORD` | obligatoire |
| `ICC_VERSION` | version de l'image (`latest`, `1.0.3`…) |
| `ICC_BIND` / `ICC_PORT` | adresse et port publiés (`127.0.0.1:8000` par défaut) |
| `CODECENTER_ADMIN_TOKEN`, `GITHUB_TOKEN` | facultatifs |
| `CODECENTER_PROXY_MODE`, `CODECENTER_PUBLIC_HOST`… | derrière un reverse proxy ([proxy.md](proxy.md)) |

Pour exposer le service, garde `ICC_BIND=127.0.0.1` et place un reverse proxy (nginx, Cloudflare Tunnel) devant ; n'ouvre jamais le port tel quel sur Internet sans HTTPS.

## Fournisseur d'IA local (Ollama, LM Studio…)

Depuis le conteneur, `127.0.0.1` désigne le conteneur lui-même. Pour joindre un Ollama installé sur l'hôte, utilise `http://host.docker.internal:11434` (ajoute `extra_hosts: ["host.docker.internal:host-gateway"]` au service `app` sous Linux). `CODECENTER_PROVIDER_NETWORK=public` interdit ces adresses locales : laisse `any` dans ce cas.

## Mise à jour

```bash
docker compose pull && docker compose up -d      # image publiée
docker compose build --pull && docker compose up -d   # depuis les sources
```

Les migrations manquantes s'appliquent toutes seules ; les volumes sont conservés.

## Durcissement inclus

Utilisateur non-root (uid 10001), système de fichiers en lecture seule (sauf `/data` et `/tmp`), `cap_drop: ALL`, `no-new-privileges`, port publié en local uniquement, mot de passe PostgreSQL obligatoire, base non exposée.

## Publication automatique (GitHub Actions)

- `.github/workflows/ci.yml` : à chaque push et pull request — cohérence des versions, en-têtes de licence, tests API (Python 3.12 et 3.13), crawler, moteur Rust, intégration PostgreSQL, puis construction **et démarrage** de l'image avec `docker compose`.
- `.github/workflows/release.yml` : à chaque tag `vX.Y.Z` — vérifie que le tag égale `VERSION`, rejoue les tests, construit l'image **amd64 + arm64**, la publie sur `ghcr.io/imperial-corporation/imperial-codecenter` (tags `X.Y.Z`, `X.Y`, `X`, `latest`) avec attestation de provenance, construit la wheel et le sdist du moteur, et crée la release GitHub avec les notes du `CHANGELOG`.

Publier une version :

```bash
python3 scripts/version.py set 1.0.4      # met à jour tous les fichiers de version
# rédige la section « ## 1.0.4 » du CHANGELOG, puis :
python3 scripts/version.py check
git commit -am "Version 1.0.4" && git tag v1.0.4 && git push origin main v1.0.4
```

À faire une fois sur GitHub : *Settings → Actions → General → Workflow permissions* : « Read and write permissions » ; après la première publication, rends le paquet public dans *Packages → imperial-codecenter → Package settings → Change visibility*.
