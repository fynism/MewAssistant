#!/usr/bin/env bash
set -euo pipefail

cd /opt/supermew

if [[ ! -f .env ]]; then
    echo "Missing .env; copy .env.docker.example to .env and set credentials first." >&2
    exit 1
fi

git pull --ff-only origin main
docker compose config --quiet
docker compose up -d --build

echo "Waiting for the application (first boot downloads the embedding model)..."
for _ in $(seq 1 90); do
    if curl --fail --silent --output /dev/null http://127.0.0.1:8000/docs; then
        docker compose ps
        echo "Deployment ready"
        exit 0
    fi
    sleep 10
done

echo "Application did not become ready. Recent logs:" >&2
docker compose logs --tail=100 app >&2
exit 1
