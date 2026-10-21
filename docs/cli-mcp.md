# Ligne de commande `icc`, jetons d'API et serveur MCP

## Jetons d'API

*Compte → Jetons d'API → Créer un jeton* : le jeton (`icc_pat_…`) n'est affiché **qu'une fois**. Il a un nom et une durée (365 jours par défaut, au plus 3650), 20 jetons par compte au maximum, révocable à tout moment.

Ce qu'il permet : la recherche, le chat, la lecture du code indexé et la configuration IA **de son compte**. Ce qu'il ne permet jamais : changer le mot de passe, supprimer le compte, créer d'autres jetons, administrer l'instance.

Sécurité : la base ne contient que l'empreinte du jeton et la clé de données du compte enveloppée par le jeton lui-même — comme une session de longue durée. La base seule ne permet donc pas de l'utiliser ; désactiver le compte ou le réinitialiser invalide ses jetons ; changer son mot de passe ne les invalide pas.

## CLI `icc`

Uniquement la bibliothèque standard Python (3.10+), aucune installation :

```bash
./scripts/icc config --url https://code.exemple.fr --token icc_pat_…   # ~/.config/icc/config.json (0600)
# ou : export ICC_URL=… ICC_TOKEN=…
./scripts/icc status
./scripts/icc search "lire un fichier json" -l python -n 5      # --json pour les scripts
./scripts/icc show <id-document> --start 10 --end 40
./scripts/icc filters
```

Chaque release GitHub contient aussi `icc.pyz`, un exécutable autonome : `python3 icc.pyz search "…"`.

`icc search` interroge l'index directement (`GET /api/v1/code/search`), **sans fournisseur IA** : c'est la recherche par mots-clés du moteur, avec extraits et numéros de ligne.

## Serveur MCP

`icc mcp` est un serveur MCP (stdio) qui expose le code de ton instance à un assistant compatible (Claude Desktop, Claude Code, etc.). Outils : `search_code`, `read_code`, `list_filters`.

```json
{
  "mcpServers": {
    "codecenter": {
      "command": "python3",
      "args": ["/chemin/vers/Imperial-CodeCenter/scripts/icc", "mcp"],
      "env": { "ICC_URL": "https://code.exemple.fr", "ICC_TOKEN": "icc_pat_…" }
    }
  }
}
```

(Claude Code : `claude mcp add codecenter -e ICC_URL=… -e ICC_TOKEN=… -- python3 /chemin/scripts/icc mcp`.) L'assistant raisonne lui-même : le serveur ne fait que chercher et lire dans ton index.

## Mesurer

`python3 scripts/bench.py --url … --token …` : latence de recherche à froid / à chaud, état de l'index, verdict et profil conseillé ([performance.md](performance.md)).
