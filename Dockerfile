# Imperial CodeCenter — https://imperial-corporation.pages.dev/icc
# Copyright (c) Imperial. Imperial Community License 1.0 (voir LICENSE) : gratuit, code ouvert, origine à conserver.
#
# Image unique : moteur Rust (wheel PyO3) + API FastAPI + interface web.
# PostgreSQL est un service séparé (voir compose.yaml).

# ---------------------------------------------------------------------------
# Étape 1 — compilation du moteur Rust et export des dépendances Python
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS builder

ENV RUSTUP_HOME=/opt/rustup \
    CARGO_HOME=/opt/cargo \
    PATH=/opt/cargo/bin:$PATH \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl ca-certificates pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable \
    && pip install "maturin>=1.9,<2.0" uv

WORKDIR /src

# Dépendances Python figées d'après api/uv.lock (sans les outils de test).
COPY api/pyproject.toml api/uv.lock api/
RUN uv export --project api --frozen --no-dev --no-hashes --no-emit-project -o /requirements.txt

# Le moteur (le plus long à compiler) : copié seul pour profiter du cache de couches.
COPY engine/ engine/
RUN maturin build --release --manifest-path engine/Cargo.toml --out /wheels

# ---------------------------------------------------------------------------
# Étape 2 — image d'exécution (sans compilateur)
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="Imperial CodeCenter" \
      org.opencontainers.image.description="Recherche de code assistée par IA (moteur Rust, API FastAPI, interface web). Basé sur Imperial CodeCenter, créé par Imperial." \
      org.opencontainers.image.url="https://imperial-corporation.pages.dev/icc" \
      org.opencontainers.image.source="https://github.com/Imperial-Corporation/Imperial-CodeCenter" \
      org.opencontainers.image.licenses="LicenseRef-Imperial-Community-1.0" \
      org.opencontainers.image.vendor="Imperial" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CODECENTER_DATA_DIR=/data \
    CODECENTER_CODE_DIR=/data/codes \
    CODECENTER_WATCH_PATHS=/data/codes \
    CODECENTER_SECRET_KEY_FILE=/data/secret.key

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client tini \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 icc \
    && useradd --system --uid 10001 --gid icc --home-dir /data --shell /usr/sbin/nologin icc \
    && install -d -o icc -g icc -m 750 /data

COPY --from=builder /requirements.txt /wheels/ /tmp/build/
RUN pip install -r /tmp/build/requirements.txt /tmp/build/*.whl && rm -rf /tmp/build

WORKDIR /app
COPY api/src/ api/src/
COPY crawler/src/ crawler/src/
COPY frontend/ frontend/
COPY examples/ examples/
COPY config/database/ config/database/
COPY LICENSE NOTICE VERSION ./
COPY docker/entrypoint.sh /usr/local/bin/icc-entrypoint
RUN chmod 0755 /usr/local/bin/icc-entrypoint

USER icc
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/icc-entrypoint"]
CMD ["uvicorn", "--app-dir", "/app/api/src", "main:app", "--host", "0.0.0.0", "--port", "8000"]
