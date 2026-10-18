# Changelog

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
