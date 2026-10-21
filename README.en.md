# Imperial CodeCenter

**Version 1.0.4** — A code and documentation search engine, assisted by AI. (Full documentation is in French: [`docs/`](docs/README.md). The web interface is available in French and English — use the **FR | EN** switch in the page footer.)

Lead developer: **yolezz** · Organisation: **Imperial** · Original idea: **HyperDemoned**

Based on **Imperial CodeCenter**, created by **Imperial** — <https://imperial-corporation.pages.dev/icc> · Repository: <https://github.com/Imperial-Corporation/Imperial-CodeCenter> · License: [Imperial Community License 1.0](LICENSE) (free of charge, open source required, paid use forbidden; the creator and members of the Imperial Corporation team are covered by a personal exception, article 5.8).

Files dropped into the watched folders are indexed automatically. A natural-language question finds the relevant files, compresses them to the model's budget, and an AI provider (local or remote) writes a structured answer with verifiable sources.

## Architecture

```
frontend/ (HTML/CSS/JS)  ──HTTP──▶  api/ (FastAPI + Python)  ──PyO3──▶  engine/ (Rust)  ──▶  PostgreSQL
                                         │                                  └─▶ files (watched folders)
                                         └──HTTP──▶ AI providers (18: Ollama, OmniRoute, Claude, OpenAI, Gemini, …)
```

Python orchestrates providers and tools; Rust handles the index and files. Python calls the Rust functions directly (no internal HTTP).

## Quick start

### Docker (simplest)

```bash
cp docker/env.exemple .env          # then set POSTGRES_PASSWORD (letters and digits)
docker compose up -d                # http://127.0.0.1:8000
```

The image is published on **GitHub Container Registry** (repository → *Packages*) for **linux/amd64 and linux/arm64**:

```bash
docker pull ghcr.io/imperial-corporation/imperial-codecenter:latest
```

Small server, old PC, Raspberry Pi, hard disk drive:

```bash
docker compose -f compose.yaml -f compose.lite.yaml up -d
```

Create your account first: **the first account is the administrator**. Without Docker, see [docs/installation.md](docs/installation.md) (`scripts/install.sh`). Podman: [docs/podman.md](docs/podman.md).

## What's new in 1.0.4 (fully backward compatible)

- **GitHub package** for amd64 + arm64, scanned (Trivy), signed (cosign), with SBOM and provenance; a **GitHub release is created automatically** on every tag. Publish with `./scripts/tag.sh [X.Y.Z]` — the version comes from the `VERSION` file, the release text from `release-notes/X.Y.Z.md`.
- **Lightweight, even on hard disks**: `CODECENTER_PROFILE=lite|minimal|auto`; a content index in PostgreSQL (searches no longer re-read hundreds of files); restarts that don't re-read the tree; indexing pauses and CPU/disk priorities; search cache; gzip and revalidated static files; a tuned PostgreSQL in `compose.lite.yaml`; `scripts/bench.py`.
- **Administration**: disable / reset / delete accounts, open or close registrations, live indexing progress, re-index, exclusion patterns (`*.min.js`, `node_modules`, …), resource monitor, Prometheus `/metrics`.
- **Backup and restore**: manifest with checksums, optional AES-256-GCM encryption, scheduled backups with rotation, offline verified restore (`python -m icc_ai.backup`).
- **Per-account API tokens**, the stdlib-only **`icc` CLI** and an **MCP server** (`icc mcp`) so an AI assistant can search your code.
- **French / English interface**, Docker secrets (`*_FILE`), Podman Quadlet, Playwright end-to-end tests, CodeQL.

Not included: a SQLite-only mode (needs an engine rewrite) and embedding-based semantic search (against the lightweight goal). "Lite" is a runtime profile of the same image, not a separate image.

## Command line and MCP

```bash
./scripts/icc config --url https://code.example.com --token icc_pat_…   # token: Account → API tokens
./scripts/icc search "read a json file" -l python -n 5
./scripts/icc mcp                                                      # MCP server (stdio)
```

An API token can search, chat and use its account's AI settings; it can never manage the account or administer the instance. See [docs/cli-mcp.md](docs/cli-mcp.md).

## Security highlights

- API keys are **bound to the account** and encrypted (AES-256-GCM) with a data key protected by the user's password (scrypt): lost password = keys must be re-entered; no other account (and not the administrator) can read them.
- Sign-in required to search, chat and configure; cross-site requests refused; SSRF guard on provider URLs; per-user and per-IP rate limits.
- Files are only opened after canonicalisation and inside the watched folders; imports are protected against zip-slip, symlinks and ZIP bombs.
- Model output is never interpreted as HTML.

See [docs/securite.md](docs/securite.md) (French).

## Tests

```bash
cd api     && uv sync && uv run pytest
cd crawler && uv run pytest
cd engine  && cargo test
pip install playwright pytest && playwright install chromium && pytest e2e
```

## License and origin

Imperial CodeCenter is distributed under the **Imperial Community License 1.0** ([LICENSE](LICENSE), [NOTICE](NOTICE)): free use and modification; any program using it must stay public and free under the same license; use in a paid project is forbidden; the origin (**Imperial**, <https://imperial-corporation.pages.dev/icc>) must remain visible. This is not an OSI-approved license and should be reviewed by a lawyer before any high-stakes use.
