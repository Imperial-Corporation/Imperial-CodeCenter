# Permissions et fichiers « introuvables »

## Le problème

Le service tourne sous un compte dédié (`imperial-codecenter`). Des fichiers ou dossiers ajoutés à la main (par `root`, ou un autre compte) dans `codes/` étaient lisibles mais **non inscriptibles** pour ce compte (`root:root 775`). Or le moteur rangeait chaque fichier dans `<catégorie>/<langage>/` : création de dossier et déplacement échouaient (`Permission denied (os error 13)`), le fichier n'était pas indexé, et l'IA ne le trouvait pas.

## Ce qui est corrigé (v1.0.0)

1. **Plus d'échec** : si le dossier n'est pas inscriptible (ou si le rangement échoue), le fichier est **indexé sur place**, avec un avertissement unique par dossier. Désactivable : `CODECENTER_ORGANIZE=off`.
2. **Nouvelles tentatives** automatiques (toutes les 60 s, 20 essais max) pour les fichiers en échec, et **rescan périodique** complet (`CODECENTER_RESCAN_SECS`, 1800 s par défaut).
3. **Rapport** : `GET /api/v1/index/health` et `doctor.sh`.
4. **Cause réglée** : `scripts/fix_permissions.sh` donne le groupe du service à l'arbre (propriétaires inchangés), dossiers en `2775` (setgid), fichiers en `664`, ACL par défaut si disponible, et ajoute ton compte au groupe du service (reconnecte-toi ensuite). Le service tourne avec `UMask=0002`.
5. **Ne jamais indexer** : `_depot/` et `.crawler/` (zone de dépôt et état du collecteur).
6. **Import par l'interface ou la zone de dépôt** : les fichiers sont écrits par le service lui-même, donc toujours inscriptibles ([depot.md](depot.md)).

## Réparer à la main

```bash
./scripts/fix_permissions.sh                 # dossier par défaut
./scripts/fix_permissions.sh /chemin/codes imperial-codecenter --dry-run
./scripts/doctor.sh
```

Si tu ajoutes des fichiers avec `sudo`, relance `fix_permissions.sh` (ou passe par la zone de dépôt).
