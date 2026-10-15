# Sécurité

- Clés API et jeton GitHub chiffrés (Fernet) ; jamais renvoyés, ni journalisés. Clé maître hors de l'arbre applicatif.
- **Pas de comptes utilisateurs.** Sans `CODECENTER_ADMIN_TOKEN`, toute personne qui atteint le serveur peut changer le fournisseur IA, importer des fichiers et lancer le crawler. En production : définis le jeton (`install.sh --generate-admin-token`) et/ou protège l'accès par le proxy.
- Requêtes inter-sites refusées sur les routes d'administration.
- Imports : noms assainis, confinement dans le dossier surveillé, protection contre zip-slip/liens symboliques/bombes ZIP, extensions et tailles limitées.
- Moteur : un chemin lu en base n'est ouvert qu'après canonicalisation et dans les dossiers surveillés.
- Rendu Markdown sans `innerHTML` ; liens limités à `http(s)`/`mailto`.
- Le contenu des fichiers est traité comme donnée dans le prompt (réduit, sans éliminer, le risque d'injection).
- Proxy : en-têtes `X-Forwarded-*` crus uniquement des proxys de confiance ; origine verrouillable aux IP Cloudflare.
- Le service tourne sous un compte dédié, `UMask=0002`.
