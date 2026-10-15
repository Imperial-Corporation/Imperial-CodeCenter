# Changelog

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
