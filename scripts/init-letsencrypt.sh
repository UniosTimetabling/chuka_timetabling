#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# scripts/init-letsencrypt.sh
# Chuka University Timetabling System
#
# Fully automates:
#   1. Installing certbot (if missing)
#   2. Freeing port 80 (stopping the dockerized nginx if it's up)
#   3. Requesting a Let's Encrypt certificate (standalone mode)
#   4. Copying fullchain.pem / privkey.pem into docker/nginx/certs
#      (the exact path docker/nginx/default.conf already expects)
#   5. Restarting the docker stack
#   6. Installing renewal hooks so certbot renew keeps everything
#      in sync automatically (stop nginx -> renew -> copy certs ->
#      start nginx) and a daily cron job to trigger it
#
# Usage:
#   sudo ./scripts/init-letsencrypt.sh -d yourdomain.com -d www.yourdomain.com -e you@example.com
#
#   Or set defaults in .env and just run:
#     SSL_DOMAIN=yourdomain.com
#     SSL_DOMAIN_WWW=www.yourdomain.com
#     SSL_EMAIL=you@example.com
#   then:
#     sudo ./scripts/init-letsencrypt.sh
#
#   Add -n to request a STAGING (test) cert first, so you don't
#   burn Let's Encrypt's real rate limits while testing:
#     sudo ./scripts/init-letsencrypt.sh -d yourdomain.com -e you@example.com -n
# ──────────────────────────────────────────────────────────────

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERTS_DIR="$PROJECT_ROOT/docker/nginx/certs"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"
ENV_FILE="$PROJECT_ROOT/.env"

DOMAINS=()
EMAIL=""
DRY_RUN=false

# ---- 0. Load defaults from .env, if present ----------------------------
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a; source "$ENV_FILE"; set +a
fi
[[ -n "${SSL_DOMAIN:-}" ]]     && DOMAINS+=("$SSL_DOMAIN")
[[ -n "${SSL_DOMAIN_WWW:-}" ]] && DOMAINS+=("$SSL_DOMAIN_WWW")
[[ -n "${SSL_EMAIL:-}" ]]      && EMAIL="$SSL_EMAIL"

# ---- 1. Parse CLI flags (these override .env) ---------------------------
ARG_DOMAINS=()
while getopts "d:e:n" opt; do
  case $opt in
    d) ARG_DOMAINS+=("$OPTARG") ;;
    e) EMAIL="$OPTARG" ;;
    n) DRY_RUN=true ;;
    *)
      echo "Usage: $0 -d domain.com [-d www.domain.com] -e you@example.com [-n]"
      exit 1
      ;;
  esac
done
[[ ${#ARG_DOMAINS[@]} -gt 0 ]] && DOMAINS=("${ARG_DOMAINS[@]}")

if [[ ${#DOMAINS[@]} -eq 0 || -z "$EMAIL" ]]; then
  echo "ERROR: no domain(s) / email supplied."
  echo "  Flags:  $0 -d yourdomain.com -d www.yourdomain.com -e you@example.com"
  echo "  .env:   SSL_DOMAIN=yourdomain.com"
  echo "          SSL_DOMAIN_WWW=www.yourdomain.com"
  echo "          SSL_EMAIL=you@example.com"
  exit 1
fi

if [[ $EUID -ne 0 ]]; then
  echo "ERROR: run this with sudo — it installs packages and binds port 80."
  exit 1
fi

echo "== Chuka Timetabling — Let's Encrypt setup =="
echo "Domains: ${DOMAINS[*]}"
echo "Email:   $EMAIL"
echo "Certs -> $CERTS_DIR"
$DRY_RUN && echo "Mode:    STAGING (test cert, not trusted by browsers)"
echo

# ---- 2. Install certbot --------------------------------------------------
if ! command -v certbot >/dev/null 2>&1; then
  echo "-> Installing certbot..."
  if ! apt-get update -y 2>/tmp/init-le-apt-err.log; then
    if [[ -f /etc/apt/sources.list.d/docker.list ]] && grep -q 'download.docker.com' /tmp/init-le-apt-err.log; then
      echo "   Removing a broken Docker apt repo entry (from a prior failed"
      echo "   get.docker.com attempt on a codename it doesn't support)..."
      rm -f /etc/apt/sources.list.d/docker.list
      apt-get update -y || { echo "ERROR: apt-get update still failing after removing docker.list. Check apt sources manually."; exit 1; }
    else
      echo "ERROR: apt-get update failed:"; tail -5 /tmp/init-le-apt-err.log
      exit 1
    fi
  fi
  apt-get install -y certbot || { echo "ERROR: certbot install failed via apt. Install it manually (https://certbot.eff.org/instructions) and re-run."; exit 1; }
else
  echo "-> certbot already installed"
fi

# ---- 3. Free up port 80 for the HTTP-01 challenge ------------------------
echo "-> Freeing port 80 for the ACME challenge..."
systemctl stop nginx 2>/dev/null || true   # in case nginx is installed outside Docker

RESTART_DOCKER_NGINX=false
if command -v docker >/dev/null 2>&1 && \
   docker compose -f "$COMPOSE_FILE" ps nginx 2>/dev/null | grep -qi "up"; then
  echo "   Stopping the dockerized nginx container temporarily..."
  (cd "$PROJECT_ROOT" && docker compose stop nginx)
  RESTART_DOCKER_NGINX=true
fi

# ---- 4. Build the -d flags for certbot -----------------------------------
DOMAIN_ARGS=()
for d in "${DOMAINS[@]}"; do
  DOMAIN_ARGS+=(-d "$d")
done

# ---- 5. Request the certificate ------------------------------------------
CERTBOT_COMMON=(certonly --standalone "${DOMAIN_ARGS[@]}" --email "$EMAIL" --agree-tos --non-interactive --keep-until-expiring)
if $DRY_RUN; then
  echo "-> Requesting a STAGING certificate (safe for repeated testing)..."
  certbot "${CERTBOT_COMMON[@]}" --staging
else
  echo "-> Requesting a real certificate from Let's Encrypt..."
  certbot "${CERTBOT_COMMON[@]}"
fi

PRIMARY_DOMAIN="${DOMAINS[0]}"
LIVE_DIR="/etc/letsencrypt/live/$PRIMARY_DOMAIN"

if [[ ! -f "$LIVE_DIR/fullchain.pem" ]]; then
  echo "ERROR: no certificate found at $LIVE_DIR — certbot must have failed above."
  # still try to bring nginx back up so the site doesn't stay down
  $RESTART_DOCKER_NGINX && (cd "$PROJECT_ROOT" && docker compose up -d nginx)
  exit 1
fi

# ---- 6. Copy certs to where docker/nginx/default.conf expects them -------
echo "-> Copying certificates into $CERTS_DIR"
mkdir -p "$CERTS_DIR"
cp "$LIVE_DIR/fullchain.pem" "$CERTS_DIR/fullchain.pem"
cp "$LIVE_DIR/privkey.pem"   "$CERTS_DIR/privkey.pem"
chmod 644 "$CERTS_DIR/fullchain.pem"
chmod 600 "$CERTS_DIR/privkey.pem"

# ---- 7. Bring the stack back up ------------------------------------------
if $RESTART_DOCKER_NGINX; then
  echo "-> Restarting dockerized nginx..."
  (cd "$PROJECT_ROOT" && docker compose up -d nginx)
fi

# ---- 8. Install renewal hooks so future renewals stay in sync ------------
mkdir -p /etc/letsencrypt/renewal-hooks/{pre,post,deploy}

cat > /etc/letsencrypt/renewal-hooks/pre/chuka-stop-nginx.sh <<HOOK
#!/bin/bash
# Frees port 80 before certbot renews.
cd "$PROJECT_ROOT" && docker compose stop nginx 2>/dev/null || true
HOOK

cat > /etc/letsencrypt/renewal-hooks/deploy/chuka-copy-certs.sh <<HOOK
#!/bin/bash
# Runs only when a cert actually renews. Copies the new files into
# the Docker-mounted certs folder used by docker/nginx/default.conf.
set -e
cp "$LIVE_DIR/fullchain.pem" "$CERTS_DIR/fullchain.pem"
cp "$LIVE_DIR/privkey.pem"   "$CERTS_DIR/privkey.pem"
chmod 644 "$CERTS_DIR/fullchain.pem"
chmod 600 "$CERTS_DIR/privkey.pem"
HOOK

cat > /etc/letsencrypt/renewal-hooks/post/chuka-start-nginx.sh <<HOOK
#!/bin/bash
# Brings nginx back up (and reloads it) after certbot finishes.
cd "$PROJECT_ROOT" && docker compose up -d nginx 2>/dev/null || true
sleep 2
docker compose exec -T nginx nginx -s reload 2>/dev/null || true
HOOK

chmod +x /etc/letsencrypt/renewal-hooks/pre/chuka-stop-nginx.sh \
          /etc/letsencrypt/renewal-hooks/deploy/chuka-copy-certs.sh \
          /etc/letsencrypt/renewal-hooks/post/chuka-start-nginx.sh

# ---- 9. Daily cron job to actually trigger the renewal check ------------
cat > /etc/cron.daily/certbot-renew-chuka <<CRON
#!/bin/bash
certbot renew --quiet
CRON
chmod +x /etc/cron.daily/certbot-renew-chuka

echo
echo "== Done =="
echo "Certificate installed at:  $CERTS_DIR/{fullchain.pem,privkey.pem}"
echo "Auto-renewal:              /etc/cron.daily/certbot-renew-chuka (checks daily, renews when <30 days left)"
echo "Renewal hooks installed:   /etc/letsencrypt/renewal-hooks/{pre,deploy,post}/chuka-*.sh"
echo
echo "Test the whole renewal pipeline safely (no real renewal happens) with:"
echo "  sudo certbot renew --dry-run"
