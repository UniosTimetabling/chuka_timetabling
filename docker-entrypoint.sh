#!/bin/sh
# ──────────────────────────────────────────────────────────────
# docker-entrypoint.sh
# Waits for MySQL to be ready, runs migrations, then starts
# the Gunicorn WSGI server.
# ──────────────────────────────────────────────────────────────
set -e

echo "==> Waiting for database..."
# Simple wait loop — retries up to 30 times (30 s total)
MAX_TRIES=30
TRIES=0
until python -c "
import os, django, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'university_timetable_system.settings')
django.setup()
from django.db import connection
connection.ensure_connection()
" 2>/dev/null; do
  TRIES=$((TRIES+1))
  if [ "$TRIES" -ge "$MAX_TRIES" ]; then
    echo "ERROR: Database not available after ${MAX_TRIES}s. Exiting."
    exit 1
  fi
  echo "   ... still waiting ($TRIES/$MAX_TRIES)"
  sleep 1
done

echo "==> Database is ready."

# ── Role dispatch ────────────────────────────────────────────
# docker-compose.yml gives celery_worker/celery_beat an explicit
# `command:` (e.g. `celery -A university_timetable_system worker ...`),
# which Docker passes to this ENTRYPOINT as "$@". The `web` service has
# no command override, so "$@" is empty there and it falls through to
# the normal migrate/static/superuser/Gunicorn boot below.
#
# IMPORTANT: this used to be missing entirely — the script always ran
# `exec gunicorn ...` no matter what, so celery_worker/celery_beat were
# silently running a second Gunicorn instead of Celery, and ImportJobs
# (and any other Celery task) just sat at PENDING forever with nothing
# consuming the queue. If you're upgrading from that version, rebuild
# the image (docker compose build) and recreate the containers — a
# volume/file-only patch of this script won't take effect until the
# image is rebuilt, since it's baked in at /app/docker-entrypoint.sh.
#
# Only ONE container (the `web` service, via the no-args branch below)
# runs migrations/static sync/superuser bootstrap, so multiple
# containers starting at the same time don't race against each other
# running `manage.py migrate` concurrently against the same MySQL
# instance.
if [ "$#" -gt 0 ]; then
  echo "==> Command override detected — skipping web bootstrap, running: $*"
  exec "$@"
fi

echo "==> Syncing static files into static_volume..."
# /app/staticfiles is a named volume mount, so whatever collectstatic
# produced in the image at /app/staticfiles_build (see Dockerfile) would
# otherwise be invisible — the volume masks it with whatever was there
# from the volume's first-ever creation. Unlike media, static files are
# pure build output (never user data), so a full overwrite is safe and
# correct: clear the volume first so stale/renamed hashed files from old
# builds don't linger, then copy everything from this build in fresh.
if [ -d /app/staticfiles_build ]; then
  rm -rf /app/staticfiles/*
  cp -r /app/staticfiles_build/. /app/staticfiles/
  echo "    + static files synced"
else
  echo "    ! /app/staticfiles_build not found — skipping sync (unexpected)"
fi

echo "==> Seeding default media files (logo, etc.) into media_volume..."
# /app/media is a named volume mount, so files baked into the image at
# /app/media get shadowed at runtime. media_defaults/ is a copy of that
# same content stored outside the mount path (see Dockerfile). Copy in
# only what's missing — cp -n never overwrites a file that already
# exists, so real user uploads already sitting in the volume are safe.
if [ -d /app/media_defaults ]; then
  find /app/media_defaults -type f | while read -r src; do
    rel="${src#/app/media_defaults/}"
    dest="/app/media/$rel"
    if [ ! -f "$dest" ]; then
      mkdir -p "$(dirname "$dest")"
      cp -n "$src" "$dest"
      echo "    + seeded $rel"
    fi
  done
fi

echo "==> Running migrations..."
# Auto-heal a specific, well-understood failure: MySQL error 1061
# "Duplicate key name '<x>'", which happens when a migration partially
# applied on an EARLIER failed/interrupted deploy already created an
# index, and this run's retry then tries to create the same index again
# from scratch as part of the same migration step. Dropping the stray
# leftover and retrying is always safe here — the migration recreates
# it correctly. Any OTHER kind of migration error is NOT auto-fixed:
# it's surfaced immediately, exactly as before, so real problems are
# never silently papered over.
MIGRATE_MAX_ATTEMPTS=5
migrate_attempt=1
migrate_log="$(mktemp)"
migrate_ok=0

while [ "$migrate_attempt" -le "$MIGRATE_MAX_ATTEMPTS" ]; do
  if python manage.py migrate --noinput > "$migrate_log" 2>&1; then
    migrate_ok=1
    break
  fi

  dup_index=$(grep -oP "Duplicate key name '\K[^']+" "$migrate_log" | head -1)

  if [ -z "$dup_index" ]; then
    echo "!!! Migration failed with an error that cannot be auto-healed: !!!"
    cat "$migrate_log"
    rm -f "$migrate_log"
    exit 1
  fi

  echo "    ! Detected a stray index left over from an earlier failed deploy: $dup_index"
  echo "    ! Dropping it and retrying migrations (attempt $migrate_attempt/$MIGRATE_MAX_ATTEMPTS)..."
  python manage.py drop_mysql_index "$dup_index"

  migrate_attempt=$((migrate_attempt + 1))
done

if [ "$migrate_ok" -ne 1 ]; then
  echo "!!! Migrations still failing after $MIGRATE_MAX_ATTEMPTS auto-heal attempts: !!!"
  cat "$migrate_log"
  rm -f "$migrate_log"
  exit 1
fi

cat "$migrate_log"
rm -f "$migrate_log"
echo "    + migrations applied"

echo "==> Ensuring admin superuser exists..."
python -c "
import os, django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'university_timetable_system.settings')
django.setup()
from django.contrib.auth.models import User

username = os.environ.get('DJANGO_SUPERUSER_USERNAME')
email    = os.environ.get('DJANGO_SUPERUSER_EMAIL', '')
password = os.environ.get('DJANGO_SUPERUSER_PASSWORD')

if not username or not password:
    print('    ! DJANGO_SUPERUSER_USERNAME/PASSWORD not set — skipping (no admin account created).')
elif User.objects.filter(is_superuser=True).exists():
    print('    - A superuser already exists — leaving accounts/passwords untouched.')
else:
    User.objects.create_superuser(username=username, email=email, password=password)
    print(f'    + Created superuser \'{username}\'.')
"

echo "==> Starting Gunicorn..."
# Defaults changed from 3 workers / 120s timeout to 2 workers / 900s.
# Program Course CSV imports (previously the main cause of a request
# running long enough to hit the old 120s timeout) now run in a Celery
# worker instead of inline (see program_management/tasks.py), so a
# Gunicorn worker is never tied up for minutes on an import any more —
# 2 workers comfortably covers normal request concurrency. The larger
# timeout is generous headroom for the other slow synchronous endpoints
# in this project (PDF generation, xlsx export, auto-allocation) that
# still run inline. Both remain fully overridable via .env / the
# environment, same as before.
exec gunicorn university_timetable_system.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-900}" \
  --log-level "${GUNICORN_LOG_LEVEL:-info}" \
  --access-logfile - \
  --error-logfile -