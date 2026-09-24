#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# scripts/db_doctor.sh — find out exactly why the app cannot reach MySQL,
# for both supported layouts:
#
#   A) database inside Docker   (compose service `db`, profile "localdb")
#   B) database outside Docker  (host MySQL on localhost, or a remote server)
#
# Usage:
#   ./scripts/db_doctor.sh              # report only, changes nothing
#   ./scripts/db_doctor.sh --provision  # create the app user + grants
#
# --provision needs a working root login on whichever server .env points at.
# It never drops anything and never touches existing data.
# ──────────────────────────────────────────────────────────────
set -uo pipefail

cd "$(dirname "$0")/.." || exit 1
ENV_FILE=".env"
PROVISION=0
[ "${1:-}" = "--provision" ] && PROVISION=1

GREEN=$'\033[0;32m'; RED=$'\033[0;31m'; YEL=$'\033[0;33m'; DIM=$'\033[2m'; OFF=$'\033[0m'
ok()   { echo "    ${GREEN}✓${OFF} $*"; }
bad()  { echo "    ${RED}✗${OFF} $*"; }
warn() { echo "    ${YEL}!${OFF} $*"; }
info() { echo "    ${DIM}$*${OFF}"; }
head_() { echo; echo "══ $* ══"; }

[ -f "$ENV_FILE" ] || { bad "No .env in $(pwd)"; exit 1; }

# Read .env without executing it (values may contain %, $, spaces).
getenv() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=//p" "$ENV_FILE" | head -1 \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//'
}

DB_NAME=$(getenv DB_NAME)
DB_USER=$(getenv DB_USER)
DB_PASSWORD=$(getenv DB_PASSWORD)
DB_HOST=$(getenv DB_HOST)
DB_PORT=$(getenv DB_PORT); DB_PORT=${DB_PORT:-3306}
DB_ROOT_PASSWORD=$(getenv DB_ROOT_PASSWORD)
DOCKER_DB_HOST=$(getenv DOCKER_DB_HOST)
DOCKER_DB_USER=$(getenv DOCKER_DB_USER)
DOCKER_DB_PASSWORD=$(getenv DOCKER_DB_PASSWORD)
APP_DB_USER=$(getenv APP_DB_USER)
APP_DB_PASSWORD=$(getenv APP_DB_PASSWORD)
COMPOSE_PROFILES=$(getenv COMPOSE_PROFILES)

EFF_DOCKER_HOST=${DOCKER_DB_HOST:-db}
EFF_DOCKER_USER=${DOCKER_DB_USER:-$DB_USER}
EFF_DOCKER_PASS=${DOCKER_DB_PASSWORD:-$DB_PASSWORD}

case "$DB_HOST" in
  localhost|127.0.0.1|::1|"") HOST_IS_LOOPBACK=1 ;;
  *) HOST_IS_LOOPBACK=0 ;;
esac

if [ "$EFF_DOCKER_HOST" = "db" ]; then MODE="in-docker"
elif [ "$EFF_DOCKER_HOST" = "host.docker.internal" ]; then MODE="host-mysql"
else MODE="remote"; fi

head_ "Configuration"
echo "    .env says            : $DB_USER@$DB_HOST:$DB_PORT/$DB_NAME"
echo "    containers will use  : $EFF_DOCKER_USER@$EFF_DOCKER_HOST:$DB_PORT/$DB_NAME"
echo "    mode                 : $MODE"
echo "    COMPOSE_PROFILES     : ${COMPOSE_PROFILES:-<unset>}"

if [ "$MODE" = "in-docker" ] && ! echo ",$COMPOSE_PROFILES," | grep -q ",localdb,"; then
  warn "Containers point at the compose service 'db', but the 'localdb' profile"
  info "is not enabled, so that service will not start. Add to .env:"
  info "    COMPOSE_PROFILES=localdb"
fi
if [ "$MODE" != "in-docker" ] && echo ",$COMPOSE_PROFILES," | grep -q ",localdb,"; then
  warn "An external database is configured, but the 'localdb' profile is on —"
  info "a second, unused MySQL container will start. Remove localdb from"
  info "COMPOSE_PROFILES unless you want both."
fi
if [ -z "$APP_DB_PASSWORD" ] || [ -z "$DB_ROOT_PASSWORD" ]; then
  warn "APP_DB_PASSWORD and/or DB_ROOT_PASSWORD are empty in .env."
  info "The db service would be created with a placeholder password. Harmless"
  info "while the localdb profile is off; set them before enabling it."
fi
if [ "$DB_USER" = "root" ] || [ "$EFF_DOCKER_USER" = "root" ]; then
  warn "The app is configured to log in as root."
  info "root's password comes from DB_ROOT_PASSWORD on a dockerised MySQL, not"
  info "from DB_PASSWORD — and a host MySQL's root is a different account again."
  info "Run: ./scripts/db_doctor.sh --provision  to switch to a dedicated user."
fi

# ── helpers ───────────────────────────────────────────────────
have() { command -v "$1" >/dev/null 2>&1; }

mysql_try() {  # host port user pass [db]
  local h=$1 p=$2 u=$3 pw=$4 db=${5:-}
  MYSQL_PWD="$pw" mysql --protocol=TCP -h "$h" -P "$p" -u "$u" \
      ${db:+"$db"} -e 'SELECT 1' >/dev/null 2>"/tmp/dbdoc.$$"
  local rc=$?
  LAST_ERR=$(tr -d '\n' < "/tmp/dbdoc.$$"); rm -f "/tmp/dbdoc.$$"
  return $rc
}

in_db_container() {  # user pass sql
  docker compose exec -T -e MYSQL_PWD="$2" db \
    mysql -h 127.0.0.1 -u "$1" -N -B -e "$3" 2>/tmp/dbdoc_c.$$
}

# ── 1. TCP reachability from this host ────────────────────────
head_ "1. Network reachability (from this host)"
PROBE_HOST=$DB_HOST; [ "$HOST_IS_LOOPBACK" = 1 ] && PROBE_HOST=127.0.0.1
if have nc; then
  if nc -z -w3 "$PROBE_HOST" "$DB_PORT" 2>/dev/null; then
    ok "$PROBE_HOST:$DB_PORT is accepting connections."
  else
    bad "Nothing is listening on $PROBE_HOST:$DB_PORT from this machine."
    if [ "$MODE" = "in-docker" ]; then
      info "Expected when the DB is inside Docker and its port is not published —"
      info "the containers reach it over the internal network, not through here."
    else
      info "Start MySQL (sudo systemctl start mysql) or check the firewall."
    fi
  fi
else
  info "nc not installed — skipping the raw port probe."
fi

# ── 2. Credentials, from this host ────────────────────────────
head_ "2. Credentials (from this host)"
if have mysql; then
  if mysql_try "$PROBE_HOST" "$DB_PORT" "$DB_USER" "$DB_PASSWORD" "$DB_NAME"; then
    ok "$DB_USER can log in and use $DB_NAME."
  else
    bad "$DB_USER was refused: $LAST_ERR"
  fi
else
  info "mysql client not installed here (sudo apt install mariadb-client) — skipped."
fi

# ── 3. Inside the db container ────────────────────────────────
if [ "$MODE" = "in-docker" ]; then
  head_ "3. Accounts inside the db container"
  if docker compose ps --status running db 2>/dev/null | grep -q chuka_db; then
    ok "chuka_db is running."
    for pair in "root:$DB_ROOT_PASSWORD:DB_ROOT_PASSWORD" \
                "root:$DB_PASSWORD:DB_PASSWORD" \
                "${APP_DB_USER:-timetabling_user}:$APP_DB_PASSWORD:APP_DB_PASSWORD" \
                "$EFF_DOCKER_USER:$EFF_DOCKER_PASS:the value containers use"; do
      u=${pair%%:*}; rest=${pair#*:}; pw=${rest%:*}; label=${rest##*:}
      [ -z "$u" ] && continue
      if [ -z "$pw" ]; then info "skipped $u (${label} is empty)"; continue; fi
      if in_db_container "$u" "$pw" "SELECT 1" >/dev/null 2>&1; then
        ok "$u + $label  -> accepted"
      else
        bad "$u + $label  -> rejected ($(tr -d '\n' </tmp/dbdoc_c.$$ 2>/dev/null | tail -c 120))"
      fi
      rm -f /tmp/dbdoc_c.$$
    done

    if [ -n "$DB_ROOT_PASSWORD" ]; then
      echo
      info "Accounts that exist (user@host), via root:"
      in_db_container root "$DB_ROOT_PASSWORD" \
        "SELECT CONCAT('      ', user, '@', host) FROM mysql.user ORDER BY user, host" 2>/dev/null \
        || info "      (could not list — root password rejected)"
      echo
      info "Databases:"
      in_db_container root "$DB_ROOT_PASSWORD" \
        "SELECT CONCAT('      ', schema_name) FROM information_schema.schemata" 2>/dev/null
    fi
  else
    bad "chuka_db is not running. Start it:  COMPOSE_PROFILES=localdb docker compose up -d db"
  fi
fi

# ── 4. From inside the application container ──────────────────
head_ "4. End-to-end check from an application container"
if docker image inspect "chuka_web:latest" >/dev/null 2>&1; then
  info "Running wait_for_db.py inside a throwaway container (20s limit)..."
  echo
  docker compose run --rm --no-deps -e DB_WAIT_TIMEOUT=20 -e DB_WAIT_INTERVAL=2 \
    web python /app/wait_for_db.py 2>&1 | sed 's/^/    /'
  echo
else
  info "chuka_web image not built yet — skipped. Build with: docker compose build web"
fi

# ── 5. Provisioning ───────────────────────────────────────────
if [ "$PROVISION" = "1" ]; then
  head_ "5. Provisioning a dedicated application user"
  NEW_USER=${APP_DB_USER:-timetabling_user}
  NEW_PASS=$APP_DB_PASSWORD
  if [ -z "$NEW_PASS" ]; then
    bad "APP_DB_PASSWORD is empty in .env. Set it first, e.g.:"
    info "    APP_DB_USER=timetabling_user"
    info "    APP_DB_PASSWORD=$(openssl rand -base64 18 2>/dev/null | tr -d '/+=' || echo 'choose-a-strong-password')"
    exit 1
  fi

  # Host patterns the app can arrive from: the docker bridge ranges, plus
  # localhost for host-side manage.py runs.
  SQL=$(cat <<SQL
CREATE DATABASE IF NOT EXISTS \`$DB_NAME\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$NEW_USER'@'%' IDENTIFIED BY '$NEW_PASS';
CREATE USER IF NOT EXISTS '$NEW_USER'@'localhost' IDENTIFIED BY '$NEW_PASS';
ALTER USER '$NEW_USER'@'%' IDENTIFIED BY '$NEW_PASS';
ALTER USER '$NEW_USER'@'localhost' IDENTIFIED BY '$NEW_PASS';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$NEW_USER'@'%';
GRANT ALL PRIVILEGES ON \`$DB_NAME\`.* TO '$NEW_USER'@'localhost';
-- needed so mysqldump can read tablespaces during the pre-deploy backup
GRANT PROCESS ON *.* TO '$NEW_USER'@'%';
GRANT PROCESS ON *.* TO '$NEW_USER'@'localhost';
FLUSH PRIVILEGES;
SQL
)
  if [ "$MODE" = "in-docker" ]; then
    echo "$SQL" | docker compose exec -T -e MYSQL_PWD="$DB_ROOT_PASSWORD" db mysql -h 127.0.0.1 -u root \
      && ok "Created/updated '$NEW_USER' inside chuka_db." \
      || { bad "Failed — root login to chuka_db was refused."; exit 1; }
  else
    read -r -s -p "    root password for the MySQL at $PROBE_HOST: " RP; echo
    echo "$SQL" | MYSQL_PWD="$RP" mysql --protocol=TCP -h "$PROBE_HOST" -P "$DB_PORT" -u root \
      && ok "Created/updated '$NEW_USER' on $PROBE_HOST." \
      || { bad "Failed — check the root password and that MySQL accepts TCP logins."; exit 1; }
  fi

  echo
  info "Now point the app at it — in .env:"
  info "    DB_USER=$NEW_USER"
  info "    DB_PASSWORD=<APP_DB_PASSWORD>"
  info "and rebuild:  docker compose up -d --force-recreate web celery_worker celery_beat"
fi

head_ "Done"
echo "    Re-run after any .env change. Full write-up: DB_CONNECTIVITY_FIX.md"
