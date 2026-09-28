#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: bash deploy.sh <staging|production> <private-env-file> <tested-image-tag-or-digest>" >&2
    exit 2
}

[[ $# -eq 3 ]] || usage
environment="$1"
env_file="$2"
image="$3"
[[ "$environment" == staging || "$environment" == production ]] || usage
[[ -f "$env_file" ]] || { echo "Environment file not found: $env_file" >&2; exit 2; }
[[ "$image" != *YOUR_DOCKERHUB* && "$image" != *:latest && "$image" != *:dev ]] || {
    echo "Use a real, immutable release tag or image digest." >&2
    exit 2
}

env_file="$(cd "$(dirname "$env_file")" && pwd)/$(basename "$env_file")"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_root"

env_value() {
    local key="$1"
    awk -F= -v name="$key" '$1 == name { sub(/\r$/, "", $2); print $2; exit }' "$env_file"
}

[[ "$(env_value SUPERMEW_ENV_NAME)" == "$environment" ]] || {
    echo "SUPERMEW_ENV_NAME must match the selected environment." >&2
    exit 2
}
volume_root="$(env_value DOCKER_VOLUME_DIRECTORY)"
[[ "$volume_root" == /* && "$(basename "$volume_root")" == "$environment" ]] || {
    echo "DOCKER_VOLUME_DIRECTORY must be absolute and end in /${environment}." >&2
    exit 2
}
for key in POSTGRES_PASSWORD REDIS_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD DATABASE_URL REDIS_URL JWT_SECRET_KEY; do
    value="$(env_value "$key")"
    [[ -n "$value" && "$value" != replace-with-* ]] || {
        echo "Set $key in $env_file before deployment." >&2
        exit 2
    }
done
[[ "$(env_value DATABASE_URL)" == *@postgres:5432/* && "$(env_value REDIS_URL)" == *@redis:6379/* && "$(env_value MILVUS_HOST)" == standalone ]] || {
    echo "Container database, cache and vector URLs must use postgres, redis and standalone service names." >&2
    exit 2
}

# Compose normally prefers inherited shell values to --env-file values.
# Discard them so this release always uses the selected environment file.
unset COMPOSE_FILE COMPOSE_PROFILES DOCKER_VOLUME_DIRECTORY APP_BIND_PORT POSTGRES_BIND_PORT
unset REDIS_BIND_PORT MILVUS_BIND_PORT MILVUS_HEALTH_BIND_PORT ATTU_BIND_PORT
unset POSTGRES_PASSWORD REDIS_PASSWORD MINIO_ROOT_USER MINIO_ROOT_PASSWORD
unset DATABASE_URL REDIS_URL JWT_SECRET_KEY POSTGRES_IMAGE REDIS_IMAGE ETCD_IMAGE
unset MINIO_IMAGE MILVUS_IMAGE ATTU_IMAGE
export SUPERMEW_ENV_FILE="$env_file" SUPERMEW_APP_IMAGE="$image"
compose=(docker compose -p "supermew-${environment}" --env-file "$env_file")
"${compose[@]}" config --quiet
"${compose[@]}" pull app
# Dependency upgrades need their own backup/maintenance window.
"${compose[@]}" up -d --wait --no-recreate --no-build --pull missing postgres redis standalone

umask 077
backup_dir="${SUPERMEW_BACKUP_DIR:-/opt/supermew-backups}/$environment"
mkdir -p "$backup_dir"
backup_file="$backup_dir/postgres-$(date -u +%Y%m%dT%H%M%SZ).dump"
"${compose[@]}" exec -T postgres sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" exec pg_dump -U postgres -d langchain_app -Fc' > "$backup_file"
echo "PostgreSQL backup: $backup_file"

# Stop the sole document-processing worker before changing its database schema.
if [[ -n "$("${compose[@]}" ps -a -q app)" ]]; then
    "${compose[@]}" stop app
fi
"${compose[@]}" run --rm --no-deps app alembic upgrade head
"${compose[@]}" up -d --wait --wait-timeout 900 --no-build app
"${compose[@]}" ps
echo "${environment} is running image ${image}"
