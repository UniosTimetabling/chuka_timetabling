#!/bin/sh
# ──────────────────────────────────────────────────────────────
# docker/export_data.sh
#
# Runs `manage.py export_data` inside the running `web` container and
# copies the resulting file out to ./exports/ on the host.
#
# Why this exists: docker-compose.yml only mounts static_volume and
# media_volume into the web container — the project source (including
# anything written to /app/exports) is NOT bind-mounted, so a file
# written inside the container is invisible on the host, and gets wiped
# out on `docker compose up --build`, unless it's explicitly copied out
# with `docker compose cp` (which is what this script does).
#
# USAGE
#   ./docker/export_data.sh                       # full JSON export
#   ./docker/export_data.sh --format xlsx         # Excel workbook
#   ./docker/export_data.sh --format csv          # zipped CSVs
#   ./docker/export_data.sh --list                # just preview, no file
#   ./docker/export_data.sh --format xlsx --apps room_management program_management
#
# Any extra arguments are passed straight through to `manage.py export_data`.
# Run from the project root (where docker-compose.yml lives).
# ──────────────────────────────────────────────────────────────
set -e

SERVICE="${DOCKER_COMPOSE_SERVICE:-web}"
CONTAINER_DIR="/app/exports"
HOST_DIR="./exports"
STAMP=$(date +%Y%m%d_%H%M%S)
CONTAINER_BASENAME="export_${STAMP}"

mkdir -p "$HOST_DIR"

echo "==> Running export_data inside the '$SERVICE' container..."
docker compose exec -T "$SERVICE" mkdir -p "$CONTAINER_DIR"
docker compose exec -T "$SERVICE" python manage.py export_data \
  --output "${CONTAINER_DIR}/${CONTAINER_BASENAME}" "$@"

# If --list was passed, export_data doesn't write a file — nothing to copy.
if printf '%s\n' "$@" | grep -q -- '--list'; then
  exit 0
fi

echo "==> Copying result out of the container to ${HOST_DIR}/ ..."
COPIED=0
for ext in json csv.zip zip xlsx; do
  SRC="${CONTAINER_DIR}/${CONTAINER_BASENAME}.${ext}"
  if docker compose exec -T "$SERVICE" test -f "$SRC" 2>/dev/null; then
    docker compose cp "${SERVICE}:${SRC}" "${HOST_DIR}/${CONTAINER_BASENAME}.${ext}"
    echo "    + ${HOST_DIR}/${CONTAINER_BASENAME}.${ext}"
    COPIED=1
  fi
done

if [ "$COPIED" -eq 0 ]; then
  echo "WARNING: no output file found in the container to copy — check the log above for errors."
  exit 1
fi

echo "==> Cleaning up temp file inside the container..."
docker compose exec -T "$SERVICE" sh -c "rm -f ${CONTAINER_DIR}/${CONTAINER_BASENAME}.*"

echo "==> Done."
