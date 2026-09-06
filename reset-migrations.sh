#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════
# .reset-migrations.sh — University Timetable System
#
# DESTRUCTIVE. One-time tool for wiping all existing Django migration
# history and the database, then regenerating a clean set of migrations
# from the current models.py files — for use BEFORE production, on a
# brand-new database with no data worth keeping.
#
# What it does, in order:
#   1. Discovers every local app that defines models — including apps that
#      have never had a migrations/ folder before — and scaffolds
#      migrations/__init__.py for any that are missing it. (An app with
#      models but no migrations folder never gets its table created, which
#      is a common cause of MySQL errno 150 "foreign key constraint is
#      incorrectly formed" when another app's model has a FK into it.)
#   2. Deletes every numbered migration file (000X_*.py) in every local
#      app's migrations/ folder (keeps __init__.py — folders stay intact).
#   3. Drops and recreates the MySQL database (reads credentials from .env
#      via Django itself, so there's only one source of truth for them).
#   4. Runs `makemigrations` to generate a fresh 0001_initial.py per app
#      straight from the current models.py.
#   5. Runs `migrate` against the new empty database.
#   6. Runs `manage.py check` as a final sanity check.
#
# This does NOT touch requirements.txt, settings.py, or any non-migration
# code. It only touches:
#   - <app>/migrations/000*.py  (deleted, regenerated)
#   - the MySQL database named in .env                (dropped, recreated)
#
# Usage:
#   ./.reset-migrations.sh            # interactive, asks to confirm twice
#   ./.reset-migrations.sh --yes      # skip confirmation prompts (CI/scripted use)
#   ./.reset-migrations.sh --keep-db  # wipe migrations only, leave DB alone
#                                      # (use this if the DB is already empty/new
#                                      #  and you don't want this script touching it)
#   ./.reset-migrations.sh --help
# ══════════════════════════════════════════════════════════════════════════
set -euo pipefail

BOLD="$(tput bold 2>/dev/null || true)"
GREEN="$(tput setaf 2 2>/dev/null || true)"
YELLOW="$(tput setaf 3 2>/dev/null || true)"
RED="$(tput setaf 1 2>/dev/null || true)"
RESET="$(tput sgr0 2>/dev/null || true)"

step()  { echo -e "\n${BOLD}${GREEN}==>${RESET} ${BOLD}$1${RESET}"; }
info()  { echo -e "    $1"; }
warn()  { echo -e "    ${YELLOW}! $1${RESET}"; }
fail()  { echo -e "${RED}ERROR:${RESET} $1" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

AUTO_YES=false
RESET_DB=true
for arg in "$@"; do
  case "$arg" in
    --yes|-y) AUTO_YES=true ;;
    --keep-db) RESET_DB=false ;;
    --help|-h)
      echo "Usage: ./.reset-migrations.sh [--yes] [--keep-db]"
      echo "  --yes       Skip confirmation prompts"
      echo "  --keep-db   Wipe and regenerate migration files only; don't touch the database"
      exit 0
      ;;
    *) fail "Unknown option: $arg (use --help)" ;;
  esac
done

# ── Use the project's own venv if it exists, else system python3 ─────────
if [ -x "$SCRIPT_DIR/env/bin/python" ]; then
  PYTHON="$SCRIPT_DIR/env/bin/python"
else
  PYTHON="python3"
fi
command -v "$PYTHON" >/dev/null 2>&1 || fail "No Python interpreter found. Run ./.deploy.sh first, or activate your venv."
info "Using interpreter: $PYTHON"

[ -f ".env" ] || fail ".env not found. Copy .env.example to .env and configure it first."
[ -f "manage.py" ] || fail "manage.py not found — run this from the project root."

# ══════════════════════════════════════════════════════════════════════════
step "0/6  Safety check"
# ══════════════════════════════════════════════════════════════════════════
echo -e "    ${RED}${BOLD}This is destructive:${RESET}"
echo -e "      - Deletes ALL migration files in every app (keeps __init__.py)"
if [ "$RESET_DB" = true ]; then
  echo -e "      - ${RED}${BOLD}DROPS and recreates the database${RESET} named in .env (DB_NAME)"
fi
echo -e "    Only do this if there is no production data you need to keep."
echo ""

if [ "$AUTO_YES" = false ]; then
  if [ ! -t 0 ]; then
    fail "Non-interactive session without --yes. Re-run with --yes to confirm explicitly."
  fi
  read -r -p "    Type 'reset' to confirm you want to proceed: " CONFIRM1
  [ "$CONFIRM1" = "reset" ] || fail "Confirmation text did not match 'reset'. Aborting, nothing was changed."
  if [ "$RESET_DB" = true ]; then
    read -r -p "    This will WIPE THE DATABASE. Type 'reset' again to confirm: " CONFIRM2
    [ "$CONFIRM2" = "reset" ] || fail "Second confirmation did not match. Aborting, nothing was changed."
  fi
else
  info "Skipping interactive confirmation (--yes passed)."
fi

# ══════════════════════════════════════════════════════════════════════════
step "1/6  Discovering local apps and ensuring each has a migrations/ folder"
# ══════════════════════════════════════════════════════════════════════════
# An app qualifies if it defines models — either a models.py file or a
# models/ package with __init__.py. This covers every local app
# automatically without hardcoding names, and — unlike checking for an
# existing migrations/ folder — it also catches apps that define models
# but have never been migrated yet (the errno 150 / "foreign key
# constraint is incorrectly formed" trap: an app with FKs into an
# un-migrated app fails because the referenced table was never created).
# For any such app we create migrations/__init__.py on the fly so it's
# picked up by makemigrations below, same as every other app.
APPS=()
NEWLY_SCAFFOLDED=()
for dir in */; do
  app="${dir%/}"
  has_models=false
  if [ -f "$app/models.py" ] || [ -f "$app/models/__init__.py" ]; then
    has_models=true
  fi
  if [ -d "$app/migrations" ] || [ "$has_models" = true ]; then
    if [ ! -f "$app/migrations/__init__.py" ]; then
      mkdir -p "$app/migrations"
      touch "$app/migrations/__init__.py"
      NEWLY_SCAFFOLDED+=("$app")
    fi
    APPS+=("$app")
  fi
done

if [ "${#APPS[@]}" -eq 0 ]; then
  fail "No local apps with models found. Are you in the project root?"
fi

info "Found ${#APPS[@]} apps: ${APPS[*]}"
if [ "${#NEWLY_SCAFFOLDED[@]}" -gt 0 ]; then
  warn "These apps had no migrations/ folder yet — created it: ${NEWLY_SCAFFOLDED[*]}"
fi

# ══════════════════════════════════════════════════════════════════════════
step "2/6  Deleting existing migration files"
# ══════════════════════════════════════════════════════════════════════════
TOTAL_DELETED=0
for app in "${APPS[@]}"; do
  # Only numbered migration files — __init__.py and __pycache__ untouched
  # by the glob; pycache cleared separately for a fully clean slate.
  shopt -s nullglob
  files=("$app"/migrations/[0-9]*.py)
  shopt -u nullglob
  count="${#files[@]}"
  if [ "$count" -gt 0 ]; then
    rm -f "${files[@]}"
    info "$app: removed $count migration file(s)"
    TOTAL_DELETED=$((TOTAL_DELETED + count))
  fi
  rm -rf "$app/migrations/__pycache__"
done
info "Total migration files deleted: $TOTAL_DELETED"

# Sanity check: every migrations/__init__.py must still exist
for app in "${APPS[@]}"; do
  [ -f "$app/migrations/__init__.py" ] || fail "$app/migrations/__init__.py is missing after cleanup — aborting before makemigrations."
done

# ══════════════════════════════════════════════════════════════════════════
if [ "$RESET_DB" = true ]; then
  step "3/6  Dropping and recreating the database"
  # ══════════════════════════════════════════════════════════════════════════
  "$PYTHON" - <<'PYEOF' || fail "Database step exited with an error — see the output above for the specific reason (e.g. non-MySQL engine, MySQLdb not installed, or bad DB credentials)."
import os, sys
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'university_timetable_system.settings')
import django
django.setup()
from django.conf import settings

db = settings.DATABASES['default']
engine = db['ENGINE']
name = db['NAME']

if 'mysql' not in engine:
    print(f"    ! DB engine is '{engine}', not MySQL — skipping automatic drop/create.")
    print(f"    ! Recreate the '{name}' database manually for this engine, then re-run with --keep-db.")
    sys.exit(1)

import MySQLdb
conn = MySQLdb.connect(
    host=db.get('HOST') or 'localhost',
    port=int(db.get('PORT') or 3306),
    user=db['USER'],
    passwd=db.get('PASSWORD') or '',
)
cur = conn.cursor()
cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
cur.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
conn.close()
print(f"    Database '{name}' dropped and recreated.")
PYEOF
  info "Database reset complete."
else
  step "3/6  Skipping database reset (--keep-db)"
  warn "Make sure the database is actually empty, or 'migrate' below may fail/conflict."
fi

# ══════════════════════════════════════════════════════════════════════════
step "4/6  Waiting for the database to accept connections"
# ══════════════════════════════════════════════════════════════════════════
MAX_TRIES=30
TRIES=0
until "$PYTHON" -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'university_timetable_system.settings')
django.setup()
from django.db import connection
connection.ensure_connection()
" 2>/dev/null; do
  TRIES=$((TRIES + 1))
  if [ "$TRIES" -ge "$MAX_TRIES" ]; then
    fail "Database not reachable after ${MAX_TRIES}s. Check .env credentials and that MySQL is running."
  fi
  sleep 1
done
info "Database connection OK."

# ══════════════════════════════════════════════════════════════════════════
step "5/6  Generating fresh migrations"
# ══════════════════════════════════════════════════════════════════════════
"$PYTHON" manage.py makemigrations "${APPS[@]}"
info "makemigrations complete."

# Verify every app actually got a 0001_initial.py — if any app's models
# didn't change relative to an empty state this would be unusual, but
# flag it rather than silently moving on.
MISSING=()
for app in "${APPS[@]}"; do
  shopt -s nullglob
  files=("$app"/migrations/[0-9]*.py)
  shopt -u nullglob
  if [ "${#files[@]}" -eq 0 ]; then
    MISSING+=("$app")
  fi
done
if [ "${#MISSING[@]}" -gt 0 ]; then
  warn "No migration was generated for: ${MISSING[*]}"
  warn "This is fine if those apps genuinely define no models; otherwise check models.py."
fi

step "6/6  Applying migrations to the fresh database"
"$PYTHON" manage.py migrate --noinput
info "Migrations applied."

echo ""
echo -e "${BOLD}${GREEN}==>${RESET} ${BOLD}Running final checks${RESET}"
"$PYTHON" manage.py check

# ══════════════════════════════════════════════════════════════════════════
echo -e "\n${BOLD}${GREEN}✓ Migration history reset and reapplied successfully.${RESET}"
echo -e "  Each app now has a single clean migration history starting from 0001_initial."
echo -e "  Next steps:"
echo -e "    python manage.py createsuperuser"
echo -e "    python manage.py collectstatic --noinput"
echo -e "  Or just run ./.deploy.sh to do the rest."
echo ""