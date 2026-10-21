# Administration

Réservée à l'**administrateur** : le premier compte créé, ou le porteur de `CODECENTER_ADMIN_TOKEN`. Elle fonctionne par cookie de session — un jeton d'API ne donne jamais accès à l'administration. Ouverture : *Compte → Administration* (le bouton n'existe que pour l'administrateur).

## Comptes

- **Inscriptions** : case « ouvertes / fermées », sans redémarrage ; le réglage est conservé et **prime sur** `CODECENTER_REGISTRATION` (qui reste la valeur initiale).
- **Désactiver / réactiver** un compte : il est déconnecté aussitôt (sessions et jetons d'API inclus) et ne peut plus se connecter ; ses données restent.
- **Réinitialiser le mot de passe** : tu fixes un nouveau mot de passe. Les clés IA du compte sont chiffrées avec l'ancien : **elles sont effacées** (à ressaisir), ainsi que ses jetons d'API et sessions ; ses discussions sont conservées. Personne, administrateur compris, ne peut lire les clés d'un compte.
- **Supprimer** un compte et ses discussions.
- Le compte administrateur ne peut être ni désactivé, ni supprimé, ni réinitialisé par ce biais.

## Index

- **Progression en direct** : fichiers vus / indexés / inchangés / en échec / exclus, fichier en cours, durée du dernier passage.
- **Relancer l'indexation** maintenant.
- **Exclusions** : un motif par ligne, fichier `<données>/exclude.txt` (ou `CODECENTER_EXCLUDE_FILE`). Sans `/` : nom de fichier ou de dossier, insensible à la casse, `*` et `?` acceptés (`*.min.js`, `node_modules`, `tmp*`). Avec `/` : suite de dossiers n'importe où sous la racine (`vendor/legacy`, `docs/*/generated`). Enregistrer lance un passage : ce qui devient exclu est **retiré de l'index** (les fichiers restent sur le disque) et ce qui ne l'est plus est **ré-indexé**. `CODECENTER_EXCLUDE` (variable) s'ajoute aux motifs du fichier.

## Système

Profil actif, durée de fonctionnement, mémoire de la machine et du processus, charge, threads, espace disque (données et bibliothèque), taille de la base des comptes. Rafraîchi toutes les 5 secondes.

## Sauvegardes

Voir [sauvegardes.md](sauvegardes.md).

## Métriques Prometheus

`GET /metrics` (format texte Prometheus). Accès : en-tête `Authorization: Bearer <jeton>` avec `CODECENTER_METRICS_TOKEN` (ou `CODECENTER_ADMIN_TOKEN`), ou administrateur connecté ; `CODECENTER_METRICS_PUBLIC=1` supprime l'authentification (réseau privé uniquement). Mesures : `codecenter_info`, `_uptime_seconds`, `_documents_total`, `_index_scan_*` (running, seen, indexed, unchanged, failed, excluded, durée), `_index_generation`, `_process_resident_bytes`, `_process_threads`, `_load1`, `_memory_available_bytes`, `_data_disk_free_bytes`, `_users`.

```yaml
scrape_configs:
  - job_name: codecenter
    metrics_path: /metrics
    authorization: { credentials: "VOTRE_JETON_DE_METRIQUES" }
    static_configs: [{ targets: ["127.0.0.1:8000"] }]
```

Routes : voir [api.md](api.md).
