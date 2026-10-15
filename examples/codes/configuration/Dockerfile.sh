#!/usr/bin/env bash
# Génère puis construit une image Docker minimale pour une application Python.
set -euo pipefail

cat > Dockerfile <<'DOCKER'
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
USER nobody
CMD ["python", "main.py"]
DOCKER

docker build -t "${1:-mon-app}:latest" .
