#!/usr/bin/env bash
set -euo pipefail

cd /opt/supermew

if [[ ! -f .env ]]; then
    echo "Missing .env; copy .env.docker.example to .env and set credentials first." >&2
    exit 1
fi

for key in SUPERMEW_APP_IMAGE POSTGRES_PASSWORD REDIS_PASSWORD; do
    if ! grep -Eq "^${key}=.+" .env; then
        echo "Missing ${key} in .env; production deployment requires an image and database passwords." >&2
        exit 1
    fi
done

git pull --ff-only origin main
docker compose config --quiet
docker compose pull
docker compose up -d --no-build

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
