# Sauvegarde et restauration

## Ce qui est sauvegardé

Une sauvegarde est une archive unique contenant :

- la **base des comptes** (copie cohérente, même en cours d'utilisation) ;
- le **dossier de données** (état du crawler, jeton GitHub chiffré…) ;
- la **clé de chiffrement** des secrets serveur (`secret.key`) ;
- facultativement la **bibliothèque de code** (`--with-codes`, volumineuse) ;
- un manifeste avec l'empreinte SHA-256 de chaque fichier.

L'index PostgreSQL n'est **pas** sauvegardé : il se reconstruit tout seul depuis les fichiers (dernière étape de la restauration : démarrer le serveur). Sauvegarde la base avec `pg_dump` si tu veux éviter la réindexation.

## Créer

Depuis l'interface : *Compte → Administration → Sauvegardes → Créer une sauvegarde* (phrase secrète facultative). Les archives sont dans `<données>/backups/` ; l'interface permet de les télécharger et de les supprimer.

En ligne de commande (sur la machine qui héberge l'instance) :

```bash
python -m icc_ai.backup create [--out DOSSIER] [--with-codes] [--passphrase-file FICHIER]
python -m icc_ai.backup list
# Docker :
docker compose exec app python -m icc_ai.backup create
```

Sans phrase secrète : `icc-backup-AAAAMMJJ-HHMMSS.tar.gz` (**en clair** : empreintes de mots de passe et configuration IA chiffrée des comptes — garde-le privé). Avec phrase secrète (8 caractères minimum) : `.icc-backup`, chiffré en AES-256-GCM par blocs, clé dérivée par scrypt ; phrase perdue = sauvegarde illisible.

## Planifier

| Variable | Rôle |
|---|---|
| `CODECENTER_BACKUP_EVERY_HOURS` | période en heures (0 = désactivé, défaut) |
| `CODECENTER_BACKUP_KEEP` | nombre de sauvegardes conservées (défaut 7) ; les plus anciennes sont supprimées |
| `CODECENTER_BACKUP_PASSPHRASE` | chiffre les sauvegardes planifiées (ou `CODECENTER_BACKUP_PASSPHRASE_FILE` avec Docker) |
| `CODECENTER_BACKUP_CODES` | `1` pour inclure la bibliothèque |

La première sauvegarde planifiée a lieu quelques minutes après le démarrage si aucune n'existe, puis à chaque période. **Copie-les hors de la machine** (autre disque, autre serveur) : une sauvegarde sur le même disque ne protège pas d'une panne du disque.

## Restaurer (serveur arrêté)

```bash
# arrête CodeCenter, puis :
python -m icc_ai.backup restore ARCHIVE [--passphrase-file F] [--with-codes] [--force]
# redémarre CodeCenter
```

- Vérifie le manifeste et toutes les empreintes **avant** de toucher à quoi que ce soit ; refuse les chemins suspects (`..`, absolus), les liens et les entrées spéciales.
- Si des données existent déjà, il faut `--force` ; les fichiers remplacés sont conservés en `*.avant-restauration`.
- Les fichiers de code déjà présents ne sont jamais écrasés.
- Docker : `docker compose stop app`, puis `docker compose run --rm --no-deps app python -m icc_ai.backup restore /data/backups/NOM --force`, puis `docker compose start app`.

La restauration se fait volontairement hors de l'interface : on ne remplace pas une base ouverte.
