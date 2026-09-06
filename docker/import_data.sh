#!/bin/sh
# ──────────────────────────────────────────────────────────────
# docker/import_data.sh
#
# Copies a JSON fixture (produced by export_data --format json) from the
# host into the running `web` container and runs `manage.py import_data`
# on it. Only JSON fixtures are importable — csv/xlsx exports are
# human-readable reports only (see export_data.py docstring).
#
# USAGE
#   ./docker/import_data.sh ./exports/export_20260813.json --dry-run
#   ./docker/import_data.sh ./exports/export_20260813.json --backup-first
#
# The first argument must be the path to the JSON file ON THE HOST.
# Any remaining arguments are passed straight through to
# `manage.py import_data` (e.g. --dry-run, --backup-first, --allow-users,
# --allow-department).
#
# Run from the project root (where docker-compose.yml lives). Always
# run with --dry-run first to see the per-model counts before actually
# loading anything.
# ──────────────────────────────────────────────────────────────
set -e

SERVICE="${DOCKER_COMPOSE_SERVICE:-web}"
CONTAINER_DIR="/app/exports"

HOST_FILE="$1"
if [ -z "$HOST_FILE" ]; then
  echo "Usage: $0 <path-to-json-fixture-on-host> [import_data options...]"
  echo "e.g.:  $0 ./exports/export_20260813.json --dry-run"
  exit 1
fi
shift

if [ ! -f "$HOST_FILE" ]; then
  echo "ERROR: file not found on host: $HOST_FILE"
  exit 1
fi

case "$HOST_FILE" in
  *.json) : ;;
  *)
    echo "ERROR: only .json fixtures (from export_data --format json) can be imported."
    echo "       csv/xlsx exports are flattened, human-readable reports — not reloadable."
    exit 1
    ;;
esac

BASENAME=$(basename "$HOST_FILE")
CONTAINER_PATH="${CONTAINER_DIR}/${BASENAME}"

echo "==> Copying $HOST_FILE into the '$SERVICE' container..."
docker compose exec -T "$SERVICE" mkdir -p "$CONTAINER_DIR"
docker compose cp "$HOST_FILE" "${SERVICE}:${CONTAINER_PATH}"

echo "==> Running import_data inside the '$SERVICE' container..."
docker compose exec -T "$SERVICE" python manage.py import_data \
  --input "$CONTAINER_PATH" "$@"

echo "==> Cleaning up temp file inside the container..."
docker compose exec -T "$SERVICE" rm -f "$CONTAINER_PATH"

echo "==> Done."
