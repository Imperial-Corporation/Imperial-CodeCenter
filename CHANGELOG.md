# Changelog

## 1.0.4
Rétrocompatible : mise à jour sans modification de configuration ni de données ([docs/compatibilite.md](docs/compatibilite.md)).

- **Paquet GitHub (ghcr.io)** : image **amd64 + arm64** publiée à chaque version (onglet *Packages*), analysée par Trivy, signée par cosign, avec attestation de provenance et SBOM ; CodeQL.
- **Publication d'une version** : `scripts/tag.sh [X.Y.Z]` (version lue dans `VERSION`, contrôles, tag annoté, push) ; la CI teste, compile et crée **automatiquement la release GitHub** avec le texte de `release-notes/X.Y.Z.md` (sinon la section du CHANGELOG) : wheels du moteur Linux amd64/arm64 (macOS universal2 et Windows au mieux), sdist, sources, CLI `icc.pyz`, SBOM, `SHA256SUMS`. `scripts/version.py` : notes depuis un fichier, `check --notes`.
- **Léger, y compris sur disque dur** : profils `CODECENTER_PROFILE` (`standard`, `lite`, `minimal`, `auto`) ; **index de contenu dans PostgreSQL** (migration `004`, `pg_trgm`) : la recherche ne relit plus des centaines de fichiers (extraits lus seulement pour les résultats affichés), avec repli automatique sans la migration ; signatures de fichiers conservées en base (**redémarrage sans relecture** de l'arbre) ; une seule lecture par fichier ; pauses d'indexation, priorités CPU/disque, pool et threads réglables ; cache de recherche ; réponses gzip et statiques revalidés (ETag) ; `compose.lite.yaml` (PostgreSQL réglé, limites mémoire/CPU) ; `scripts/bench.py`.
- **Administration** : comptes (désactiver, réactiver, réinitialiser le mot de passe, supprimer), inscriptions ouvertes/fermées sans redémarrage, progression de l'indexation en direct, réindexation, **exclusions** (`*.min.js`, `node_modules`…) appliquées aussitôt, moniteur de ressources, **`/metrics` Prometheus**.
- **Sauvegarde / restauration** : archive avec manifeste et empreintes, chiffrement optionnel (AES-256-GCM, scrypt), sauvegardes planifiées avec rotation, création depuis l'interface, restauration vérifiée hors ligne (`python -m icc_ai.backup`).
- **Jetons d'API par compte** (limités à la recherche, au chat et à la configuration IA du compte), CLI **`icc`** (stdlib seule), routes `/api/v1/code/*` sans IA et **serveur MCP** (stdio).
- **Interface FR / EN** (sélecteur en pied de page, langue du navigateur par défaut) ; `README.en.md`.
- **Conteneurs** : secrets par fichier (`*_FILE`, `compose.secrets.yaml`), **Podman** (Quadlet), `PYTHONPATH` pour les commandes dans le conteneur.
- **Qualité** : tests e2e Playwright en CI, tests de compatibilité 1.0.3 → 1.0.4 (routes figées, base des comptes migrée, base PostgreSQL sans migration 004), tests d'intégration étendus.
- **Correctifs** : texte périmé du panneau IA (« configuration globale » alors que les clés sont par compte depuis la 1.0.2).
- **Non inclus** : mode SQLite seul (réécriture du moteur) et recherche sémantique par embeddings (contraire à l'objectif de légèreté) ; le « profil léger » est un profil d'exécution de la même image, pas une image distincte.

## 1.0.3
- **Docker** : `Dockerfile` multi-étapes (moteur Rust compilé dans une étape dédiée, image finale sans compilateur, utilisateur non-root `uid 10001`, `tini`, contrôle de santé) et `compose.yaml` avec PostgreSQL 16 (mot de passe obligatoire, base non exposée, système de fichiers en lecture seule, `cap_drop: ALL`, port publié en local). Au démarrage : clé de chiffrement persistante dans le volume, attente de PostgreSQL, migrations appliquées une seule fois, exemples copiés une seule fois. Guide : `docs/docker.md`.
- **Publication automatique (GitHub Actions)** : `ci.yml` (versions, en-têtes de licence, tests API Python 3.12/3.13, crawler, moteur Rust, intégration PostgreSQL, construction et démarrage de l'image) ; `release.yml` à chaque tag `vX.Y.Z` (vérification du tag, tests, image amd64 + arm64 sur `ghcr.io/imperial-corporation/imperial-codecenter` avec attestation de provenance, wheel et sdist du moteur, release GitHub avec les notes du CHANGELOG) ; Dependabot. `scripts/version.py` (`check`, `set`, `notes`) garde toutes les versions cohérentes.
- **Licence** : cas particulier de l'équipe Imperial (article 5.8, définition 1.11) : les obligations de code ouvert, de gratuité et de non-commercialisation (articles 4 et 5) et l'interdiction de double licence (13.3) ne s'appliquent pas au créateur ni aux membres de l'équipe Imperial Corporation ; exception personnelle, non transmissible, sans effet sur les tiers ; l'attribution reste due. Reprise dans `NOTICE`, `docs/licence.md`, `engine/LICENSE` et la traduction anglaise.
- **Crawler en direct** : événements de progression enrichis (phase, dépôts faits / à faire, dépôt en cours, fichiers du dépôt, quotas, erreurs, derniers fichiers), émis chaque seconde et à chaque changement de dépôt ; panneau avec barre de progression, phase, vitesse, temps restant estimé et liste des derniers fichiers ; interrogation du serveur chaque seconde pendant une collecte.
- **Correctifs** : `localhost` qui se résout en IPv6 (`::1`) n'est plus refusé comme fournisseur IA (Ollama, LM Studio…) ; les adresses IPv4 encapsulées en IPv6 sont jugées comme l'IPv4 ; les tests de syntaxe nginx n'exigent plus les droits root (ports hauts pendant le test) ; le test de l'identité du projet ne demande plus le moteur compilé.

## 1.0.2
- **Clés liées au compte et chiffrées par le mot de passe** : chaque compte a sa propre clé de données (aléatoire), protégée par une clé dérivée du mot de passe (scrypt) ; la configuration IA (fournisseur, modèle, clés API) est chiffrée en AES-256-GCM et n'est lisible que par son propriétaire. Pas de compte, pas d'IA ; on ne peut pas utiliser les clés d'un autre compte. Changer de mot de passe réencrypte la clé ; **mot de passe oublié = clés à ressaisir**. L'ancienne configuration globale de la 1.0.1 est ignorée.
- **Connexion obligatoire** pour la recherche, la discussion et la configuration ; les discussions sont rattachées à leur créateur (les autres comptes reçoivent « introuvable »).
- **Sécurité** : garde SSRF sur les URL de fournisseur (`CODECENTER_PROVIDER_NETWORK=any|public`), limite par utilisateur (`CODECENTER_USER_RATE`, 30/min), limite d'inscriptions (10/h/IP), `Cache-Control: no-store` sur `/api/`, contrôle d'origine sur les routes qui modifient, administration (bibliothèque, crawler) réservée au premier compte ou au jeton `CODECENTER_ADMIN_TOKEN`.
- **IA** : exploite davantage ses outils (seuils de profondeur et consigne revus : plusieurs recherches, lecture des fichiers, recoupement avant de répondre).
- **Interface** : boutons de configuration et du crawler déplacés dans la barre supérieure (ils ne bougent plus au début d'une discussion) ; interface du crawler épurée ; le bouton « Enregistrer » est toujours actif et confirme ou explique par une notification.
- **Compilation** : licence copiée dans `engine/` et `license-file = "LICENSE"` dans `Cargo.toml` (maturin compile de nouveau) ; dépôt : https://github.com/Imperial-Corporation/Imperial-CodeCenter.
- **Nouveaux exemples de base** : 31 scripts (tri fusion, BFS/DFS, table de hachage, arbre binaire, pagination, client HTTP avec retry, limiteur de débit, files et mutex, GitHub Actions, Makefile, CSV en flux, regex, journalisation, hachage de mot de passe, validation, HMAC, échappement HTML, TCP/timeout HTTP, tests pytest/go/cargo) ; total porté à 66.

## 1.0.1
- **Comptes** : inscription / connexion par pseudo + mot de passe (empreinte scrypt salée), cookie de session `HttpOnly` / `SameSite=Lax` (`Secure` derrière HTTPS), protection contre la force brute, changement de mot de passe, suppression du compte.
- **Discussions définitives** : une discussion est temporaire jusqu'à ce qu'on l'enregistre ; les discussions enregistrées s'ouvrent depuis une barre latérale gauche qui se déplie au survol, et la suite s'y ajoute automatiquement.
- **Interface** : dès qu'une question est posée, l'écran d'accueil s'efface pour laisser place à la discussion ; blocs de réponse plus propres ; code coloré (coloration syntaxique sûre) ; la section « Ajouter des scripts » est supprimée de l'interface.
- **Recherche IA plus sobre** : seuls les fichiers réellement pertinents sont gardés et joints au contexte (seuils relatifs au meilleur score selon la profondeur), moins d'outils, moins de tokens.
- **12 nouveaux fournisseurs IA** : OpenRouter, Mistral, DeepSeek, xAI, Together AI, Fireworks AI, Cerebras, NVIDIA NIM, Cohere, LM Studio, llama.cpp / vLLM, compatible OpenAI libre.
- **Installation automatique** de rustup, rustc et cargo (`scripts/lib/toolchain.sh`, appelé par `deploy.sh`, `build.sh`, `release.sh`) et de cloudflared quand le mode Cloudflare est utilisé (`--tunnel-token` pour le service du tunnel).
- **Licence** : Imperial Community License 1.0 (`LICENSE`, `NOTICE`, `docs/licence.md`) ; en-têtes de fichiers, `GET /api/v1/about`, pied de page de l'interface, métadonnées des paquets.
- Version portée à 1.0.1 partout.

## 1.0.0
- Import de scripts par l'interface (tags, ZIP) et zone de dépôt `_depot/` (tags.txt, `.tags`, sous-dossiers).
- Indexation robuste aux permissions : indexation sur place, nouvelles tentatives, rescan périodique, rapport de santé ; `fix_permissions.sh`, `doctor.sh`.
- Crawler : planification, arrêt à heure / durée / volume / taille de bibliothèque, sécurité disque, jeton chiffré, panneau dans l'interface, état restauré au redémarrage.
- Intégration Nginx / Cloudflare activable (`setup_proxy.sh`, middleware de proxys de confiance, limitation de débit, en-têtes de sécurité).
- IA : Markdown sûr, chatbox avec outils, pistes de suite, profondeur adaptative (aperçu / ciblé / approfondi), conversations en mémoire.
- Correctif OmniRoute (SSE, réponse HTML).
- `install.sh` tout-en-un ; 35 scripts d'exemple ; documentation dans `docs/`.
- Correctif : boucle infinie du rendu Markdown (expression régulière partagée dans un appel récursif).
- Moteur 23 tests, API 555 tests, crawler 254 tests.
