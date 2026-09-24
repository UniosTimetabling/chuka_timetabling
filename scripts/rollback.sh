#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# scripts/rollback.sh
# Reverts the web app to the last image tagged by
# deploy_production_webserver.sh before its most recent build.
#
# Usage:
#   sudo ./scripts/rollback.sh
#   sudo ./scripts/rollback.sh --yes     # non-interactive
# ──────────────────────────────────────────────────────────────

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE_NAME="chuka_web"
MAINTENANCE_FLAG="$PROJECT_ROOT/docker/nginx/maintenance/maintenance.flag"
AUTO_YES=false
[[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]] && AUTO_YES=true

BOLD="$(tput bold 2>/dev/null || true)"; RED="$(tput setaf 1 2>/dev/null || true)"
GREEN="$(tput setaf 2 2>/dev/null || true)"; RESET="$(tput sgr0 2>/dev/null || true)"
info() { echo "    $1"; }
ok()   { echo "    ${GREEN}✓${RESET} $1"; }
fail() { echo "${RED}✗ ERROR:${RESET} $1" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "Run this with sudo — it manages Docker containers."

if ! docker image inspect "${IMAGE_NAME}:previous" >/dev/null 2>&1; then
  fail "No ${IMAGE_NAME}:previous image found. There's nothing to roll back to (either this is the first-ever deploy, or a rollback already used up the last saved image)."
fi

CURRENT_ID=$(docker image inspect "${IMAGE_NAME}:latest" --format '{{.Id}}' 2>/dev/null || echo "none")
PREVIOUS_ID=$(docker image inspect "${IMAGE_NAME}:previous" --format '{{.Id}}' 2>/dev/null || echo "unknown")

echo "${BOLD}This will roll back ${IMAGE_NAME}:latest to the previous build.${RESET}"
info "Current : $CURRENT_ID"
info "Rollback: $PREVIOUS_ID"

if [[ "$AUTO_YES" != "true" ]]; then
  read -r -p "    Proceed? [y/N] " reply
  [[ "$reply" =~ ^[Yy] ]] || { info "Cancelled."; exit 0; }
fi

cd "$PROJECT_ROOT"

info "Showing the maintenance page for the duration of the rollback..."
mkdir -p "$(dirname "$MAINTENANCE_FLAG")"
date -Iseconds > "$MAINTENANCE_FLAG"

info "Re-tagging ${IMAGE_NAME}:previous -> ${IMAGE_NAME}:latest..."
docker tag "${IMAGE_NAME}:previous" "${IMAGE_NAME}:latest"

info "Recreating the web container with the rolled-back image..."
docker compose up -d --force-recreate --no-deps web

info "Waiting for it to become healthy..."
waited=0
until [[ "$(docker compose ps --format '{{.Service}} {{.Health}}' | awk '$1=="web"{print $2}')" == "healthy" ]]; do
  sleep 5; waited=$((waited + 5))
  [[ $waited -ge 120 ]] && fail "Rolled-back container did not become healthy within 120s. Check: docker compose logs web"
done

# nginx doesn't need to be recreated, just make sure it's still fine
docker compose up -d --no-deps nginx >/dev/null 2>&1 || true

rm -f "$MAINTENANCE_FLAG"
ok "Maintenance mode turned off — the rolled-back site is live again."

mkdir -p "$PROJECT_ROOT/logs"
echo "$(date -Iseconds)  ROLLBACK to=$PREVIOUS_ID  by=$(whoami)  host=$(hostname)" >> "$PROJECT_ROOT/logs/deploy_history.log"

ok "Rollback complete. ${IMAGE_NAME}:latest now points at the previous build."
info "Note: a second rollback right now won't do anything further back — only one prior build is retained as ':previous'."
docker compose ps
