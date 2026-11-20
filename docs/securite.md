# Sécurité

- Clés API et jeton GitHub chiffrés (Fernet) ; jamais renvoyés, ni journalisés. Clé maître hors de l'arbre applicatif.
- **Comptes** (obligatoires pour chercher, discuter et configurer ; [comptes.md](comptes.md)) : mots de passe scrypt, session par cookie `HttpOnly` / `SameSite=Lax`, jeton stocké en empreinte, limitation des tentatives, requêtes inter-sites refusées. Chaque compte a une clé de données aléatoire protégée par son mot de passe (AES-256-GCM) : clés API isolées par compte, irrécupérables si le mot de passe est oublié. Administration = premier compte ou `CODECENTER_ADMIN_TOKEN`. Garde SSRF sur les URL de fournisseur, limites par utilisateur et par IP.
- Les routes d'administration n'utilisent pas les comptes. Sans `CODECENTER_ADMIN_TOKEN`, toute personne qui atteint le serveur peut changer le fournisseur IA, importer des fichiers et lancer le crawler. En production : définis le jeton (`install.sh --generate-admin-token`) et/ou protège l'accès par le proxy.
- Requêtes inter-sites refusées sur les routes d'administration.
- Imports : noms assainis, confinement dans le dossier surveillé, protection contre zip-slip/liens symboliques/bombes ZIP, extensions et tailles limitées.
- Moteur : un chemin lu en base n'est ouvert qu'après canonicalisation et dans les dossiers surveillés.
- Coloration syntaxique et rendu Markdown sans `innerHTML` ; liens limités à `http(s)`/`mailto`.
- Le contenu des fichiers est traité comme donnée dans le prompt (réduit, sans éliminer, le risque d'injection).
- Proxy : en-têtes `X-Forwarded-*` crus uniquement des proxys de confiance ; origine verrouillable aux IP Cloudflare.
- Le service tourne sous un compte dédié, `UMask=0002`.

## 1.0.4

- **Jetons d'API** : l'empreinte seule est en base ; la clé de données du compte y est enveloppée par le jeton (comme une session). Ils n'ouvrent ni la gestion du compte, ni l'administration ; ils sont révoqués avec la désactivation ou la réinitialisation du compte.
- **Administration** : cookie de session ou `CODECENTER_ADMIN_TOKEN` uniquement ; le compte administrateur est protégé contre sa propre désactivation/suppression ; la réinitialisation d'un mot de passe efface les clés chiffrées du compte (l'administrateur ne peut jamais les lire).
- **Sauvegardes** : instantané SQLite cohérent, chiffrement AES-256-GCM par blocs (scrypt) avec phrase secrète, dossier en `0700` et fichiers en `0600`, restauration qui valide le manifeste et refuse chemins et liens suspects avant d'écrire. Une sauvegarde sans phrase secrète est sensible.
- **Métriques** : jeton dédié, comparaison en temps constant ; `CODECENTER_METRICS_PUBLIC` seulement sur réseau privé.
- **Chaîne de publication** : scan Trivy, signature cosign sans clé, attestation de provenance, SBOM, CodeQL, Dependabot.
- **Conteneur** : secrets par fichier (`*_FILE`), Podman rootless possible.

## 1.0.5

Audit ciblé du crawler, de la bibliothèque et de l'analyse IA ; seconde passe après correctifs (aucune régression, 713 tests API et la suite du crawler passent).

### MEDIUM — Modification de fichiers collectés (nouvelle surface)
Impact potentiel : écriture hors de la destination, écrasement concurrent. Correctif : chemins confinés sous la destination, liens symboliques et traversées refusés, fichiers réguliers seulement, écriture atomique, empreinte attendue obligatoire (409), taille limitée. Statut : corrigé, testé.

### MEDIUM — Analyse IA et coûts
Impact potentiel : consommation abusive du fournisseur, injection par le contenu d'un fichier. Correctif : lancement manuel, 25 fichiers par lancement, plafond quotidien, contenu tronqué traité comme donnée, réponse JSON validée strictement (catégories connues, noms de dossier sûrs), échec du fournisseur = arrêt du lot. Statut : corrigé, testé. Risque résiduel : un modèle peut mal classer un fichier (visible et corrigeable à la main).

### MEDIUM — Mode UNSAFE
Impact potentiel : disque saturé, fuite de secrets collectés. Correctif : espace libre minimal d'au moins 1 Go, exclusion des secrets inchangée, attente des limites de débit, rejet de toute demande contradictoire. Statut : corrigé, testé. Le mode reste volontairement sans plafond de volume : l'administrateur en est responsable.

### LOW — Paramètres ignorés en silence
Un champ de formulaire inconnu retombait sur une valeur par défaut. Correctif : champs inconnus refusés (422) et paramètres appliqués vérifiés. Statut : corrigé.

### INFO
Icônes : sous-ensemble Lucide (ISC) embarqué localement, aucun CDN (la CSP n'autorise que l'origine). Le crawler a été testé avec des réponses GitHub simulées, pas contre le vrai GitHub.
