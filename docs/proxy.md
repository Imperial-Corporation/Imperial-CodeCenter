# Production : Nginx et Cloudflare

Désactivé par défaut. À activer pour une exposition publique.

## Activer

```bash
./scripts/setup_proxy.sh enable --mode cloudflare --server-name code.exemple.fr
./scripts/setup_proxy.sh enable --mode nginx --server-name code.exemple.fr \
      --tls-cert /etc/letsencrypt/live/code.exemple.fr/fullchain.pem \
      --tls-key  /etc/letsencrypt/live/code.exemple.fr/privkey.pem
./scripts/setup_proxy.sh status | disable | print
```

`enable` génère la configuration nginx, la **teste (`nginx -t`) avant de l'activer** (retour arrière en cas d'erreur) et écrit `CODECENTER_PROXY_MODE` & co. dans `/etc/<service>/.env`, puis redémarre le service. `disable` revient au mode direct.

Avec `--mode cloudflare`, **cloudflared** est installé automatiquement s'il manque (dépôt apt officiel `pkg.cloudflare.com`, sinon binaire des versions GitHub). `--tunnel-token JETON` installe aussi le service du tunnel Cloudflare ; `--no-cloudflared` saute cette installation. Le jeton est celui du tunnel créé dans le tableau de bord Cloudflare Zero Trust.

## Ce que fait chaque mode

| | `nginx` | `cloudflare` |
|---|---|---|
| IP réelle du visiteur | `X-Forwarded-For` / `X-Real-IP` | `CF-Connecting-IP` |
| Origine verrouillée aux IP Cloudflare | — | oui (`return 444` sinon), `--no-lock` pour désactiver |
| HTTPS | optionnel (`--tls-cert/--tls-key`) | géré par Cloudflare (SSL « Full ») |

Côté application (`proxy.py`) : IP, schéma et hôte ne sont crus que s'ils viennent d'un **proxy de confiance** ; hôtes autorisés (400 sinon), limite de débit par IP réelle (429, `CODECENTER_RATE_LIMIT`), en-têtes de sécurité et CSP, HSTS avec `CODECENTER_FORCE_HTTPS=1`. `GET /api/v1/proxy/check` montre ce que l'application voit.

Plages Cloudflare : `config/proxy/cloudflare-ips.txt` (22 réseaux, vérifiées avec la liste officielle) ; `update_cloudflare_ips.sh` les rafraîchit.

## Vérification

La configuration produite a été validée avec `nginx -t` (nginx 1.24) pour les 4 variantes, **IPv6 désactivé** (le bac à sable de test n'avait pas d'IPv6 ; sur ton serveur, laisse l'IPv6 activé si disponible). Aucun test n'a été fait avec un vrai domaine Cloudflare, et l'installation de cloudflared n'a pas pu être testée en ligne dans le bac à sable (commandes écrites d'après la documentation officielle).
