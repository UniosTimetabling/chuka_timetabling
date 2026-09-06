#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 08_maintenance_mode.sh — turns the "we'll be right back" page
# on/off during a deploy, so visitors never hit a raw 502/504 or
# "connection refused" while the web container is being rebuilt,
# recreated, and re-migrated.
#
# How it works: docker/nginx/default.conf checks for the flag
# file below on every request and, if present (or if the app
# upstream itself errors with 502/503/504), serves
# docker/nginx/maintenance/index.html with a 503 instead of
# proxying to Django. Both the flag file and the page live under
# docker/nginx/maintenance/, bind-mounted read-only into the
# nginx container — flipping the flag on the HOST takes effect
# on nginx's very next request, no reload/restart needed.
#
# Deliberately NOT auto-disabled on failure: if disable_maintenance_mode
# is never reached (script exits via `fail` before the containers report
# healthy) the page stays up rather than exposing a half-deployed site.
# It's turned back off as soon as Docker itself confirms both containers
# healthy (see deploy_production_webserver.sh) — not gated on the
# nginx-routed smoke tests that run afterward, since those would forever
# see the maintenance page's own 503 while the flag stayed up. See the
# EXIT trap in deploy_production_webserver.sh for the failure-path safety net.
# ──────────────────────────────────────────────────────────────

MAINTENANCE_FLAG="$PROJECT_ROOT/docker/nginx/maintenance/maintenance.flag"

enable_maintenance_mode() {
  step "8/11  Enabling maintenance mode"
  mkdir -p "$(dirname "$MAINTENANCE_FLAG")"
  date -Iseconds > "$MAINTENANCE_FLAG"

  if docker compose ps -q nginx 2>/dev/null | grep -q .; then
    ok "Maintenance page is live — visitors now see 'be right back' instead of errors for the rest of this deploy."
  else
    info "nginx isn't running yet (first-ever deploy) — the flag is set and will apply as soon as it starts."
  fi
}

disable_maintenance_mode() {
  if [[ -f "$MAINTENANCE_FLAG" ]]; then
    rm -f "$MAINTENANCE_FLAG"
    ok "Maintenance mode turned off — the live site is serving real traffic again."
  fi
}
