# Podman (Quadlet)

Les fichiers de `docker/podman/` décrivent CodeCenter pour **Podman + systemd** (rootless possible), sans compose.

```bash
mkdir -p ~/.config/containers/systemd
cp docker/podman/* ~/.config/containers/systemd/

# secrets (pas de mot de passe dans les fichiers)
openssl rand -hex 24 | tr -d '\n' | podman secret create icc_db_password -
printf 'postgresql://codecenter:%s@icc-db:5432/imperial_codecenter' "$(podman secret inspect icc_db_password --showsecret --format '{{.SecretData}}')" \
  | podman secret create icc_database_url -

systemctl --user daemon-reload
systemctl --user start icc-app          # démarre aussi icc-db
loginctl enable-linger "$USER"          # pour qu'il démarre sans session ouverte
```

Interface : <http://127.0.0.1:8000>. Les volumes `icc-db` et `icc-data` sont créés automatiquement. Pour le profil léger, décommente les lignes `Environment=CODECENTER_PROFILE=lite` / `Memory=` dans les fichiers. `AutoUpdate=registry` permet `podman auto-update` pour suivre les nouvelles images publiées.

L'image est la même que pour Docker ; les secrets arrivent par `DATABASE_URL` (variable) ou `DATABASE_URL_FILE` (fichier), voir [docker.md](docker.md).
