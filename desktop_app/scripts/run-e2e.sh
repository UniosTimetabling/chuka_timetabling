#!/usr/bin/env bash
# Full desktop-sync check on a throwaway database:
#   migrate + seed (desktop_sync.e2e_settings) -> start the Django dev server -> run the headless sync test -> stop server.
# Run from anywhere; needs the project's Python environment active (or PYTHON=/path/to/python) and `npm install` done.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="$(cd "$HERE/.." && pwd)"
PYTHON="${PYTHON:-python}"
PORT="${E2E_PORT:-8765}"
export DESKTOP_E2E_DB="${DESKTOP_E2E_DB:-/tmp/desktop_e2e.sqlite3}"
export DJANGO_SETTINGS_MODULE=desktop_sync.e2e_settings
export PYTHONPATH="$PROJECT${PYTHONPATH:+:$PYTHONPATH}"

if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then echo "Port $PORT is already in use (a leftover dev server?). Stop it or set E2E_PORT." >&2; exit 1; fi
rm -f "$DESKTOP_E2E_DB"
cd "$PROJECT"
"$PYTHON" manage.py migrate -v 0
"$PYTHON" manage.py seed_desktop_e2e
"$PYTHON" manage.py runserver "127.0.0.1:$PORT" --noreload >/tmp/desktop_e2e_server.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 40); do
  curl -s -o /dev/null "http://127.0.0.1:$PORT/api/desktop/auth/login/" && break
  sleep 0.5
done

cd "$HERE"
E2E_BASE_URL="http://127.0.0.1:$PORT" E2E_PY="$PYTHON" E2E_PROJECT="$PROJECT" E2E_SETTINGS="$DJANGO_SETTINGS_MODULE" E2E_PYTHONPATH="$PYTHONPATH" \
  npm run test:sync
