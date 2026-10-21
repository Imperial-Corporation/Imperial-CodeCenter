# Compatibilité ascendante (1.0.3 → 1.0.4)

La 1.0.4 **n'impose rien** : mets l'image ou le code à jour, redémarre, c'est tout. Ce qui est garanti :

| Domaine | Garantie |
|---|---|
| API HTTP | toutes les routes de la 1.0.3 existent, mêmes chemins et mêmes réponses ; seules des routes et des clés **s'ajoutent** (`is_admin` dans `/auth/me` pour un utilisateur connecté). Verrouillé par `api/tests/test_compat.py`. |
| Comptes | `accounts.db` de la 1.0.3 s'ouvre tel quel : colonnes et tables ajoutées au démarrage, rien n'est perdu, mots de passe, clés et discussions inchangés. |
| Base PostgreSQL | la migration `004_content_index.sql` est additive et rejouable (`IF NOT EXISTS`). Appliquée par `release.sh` et par le conteneur. Sans elle (ou sans `pg_trgm`), le moteur fonctionne comme en 1.0.3. |
| Réglages | toutes les variables existantes gardent leur sens et leur défaut. Les nouvelles sont facultatives. Sans `CODECENTER_PROFILE`, aucun réglage de ressources n'est imposé. |
| Docker | mêmes volumes (`icc-db`, `icc-data`), même `compose.yaml` ; `compose.lite.yaml` et `compose.secrets.yaml` sont des **ajouts** facultatifs. |
| Inscriptions | `CODECENTER_REGISTRATION` reste la valeur initiale ; dès que l'administrateur utilise la case de l'interface, son réglage prime. |

## À savoir

- **Première indexation après la mise à jour** : les documents déjà indexés n'ont pas encore de contenu en base ; leur contenu est ajouté lors du premier passage (tant qu'il manque, la recherche lit ce fichier sur le disque comme avant). Compte un passage complet sur la bibliothèque.
- La base grossit d'environ la taille du code indexé (`CODECENTER_CONTENT_INDEX=off` pour s'en passer).
- Les jetons d'API et l'administration sont nouveaux : rien à migrer.
- Retour arrière : redéployer la 1.0.3 fonctionne ; les colonnes/tables ajoutées sont ignorées par l'ancienne version.
