#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════════════════
# deploy_production_webserver.sh
# Chuka University Timetabling System — full production deploy orchestrator
#
# This is the ONLY script you run by hand. It wires together every step
# in scripts/z_omega_deployment_script/ in order, stops on the first
# failure with a clear message, and asks for confirmation before doing
# anything destructive or irreversible.
#
#   1. Detect OS — any Linux distro is treated as a production target
#      (Ubuntu, Debian, Kali, Arch, Alpine, whatever an IoT/edge box
#      ships). Only apt-based distros get automated dependency install
#      in step 2; others just need docker/curl/openssl/certbot present
#      already, via their own package manager.
#   2. Check & install dependencies (Docker, Compose, certbot, curl...)
#   3. Check required ports are free (80, 443, 3306 local-only)
#   4. Configure the host firewall (ufw: allow SSH/80/443, deny the rest)
#   5. Configure .env interactively, show a summary, require confirmation
#   6. Obtain SSL certs (Let's Encrypt for real domains, self-signed for
#      --skip-ssl/test runs) — done BEFORE Docker so nginx never starts
#      pointed at missing cert files
#   7. Back up the database + media to backups/<timestamp>/ before
#      anything disruptive happens (skipped cleanly on a first deploy)
#   8. Show visitors the branded "be right back" maintenance page via
#      nginx instead of a raw error/timeout for the rest of the deploy
#   9. docker compose build && up, tagging the previous image for instant
#      rollback (./scripts/rollback.sh), waiting for healthchecks
#  10. Smoke-test the live stack as an extra check (health endpoint, HTTPS,
#      DB, static files) — runs AFTER maintenance mode is already off, since
#      it's turned off as soon as Docker itself reports the containers
#      healthy, not gated on these checks
#  11. Tailscale Funnel — only when there's no public domain yet (step 5
#      asks; --public-domain/--private-domain to skip the prompt). Gives
#      the mobile app a stable way to reach this server from outside the
#      LAN. Re-asserted on every run, including plain re-deploys/updates,
#      so it can't silently drop after a reboot or a Tailscale update.
#
# Usage:
#   sudo ./deploy_production_webserver.sh                  # full interactive prod deploy
#   sudo ./deploy_production_webserver.sh --skip-ssl --yes # quick local/Kali test run
#   sudo ./deploy_production_webserver.sh --staging-ssl    # dry-run real domain safely
#   sudo ./deploy_production_webserver.sh --private-domain # force Tailscale Funnel on, no prompt
#   sudo ./deploy_production_webserver.sh --public-domain  # force Tailscale Funnel off, no prompt
#   sudo ./deploy_production_webserver.sh --help
#
# Safe to re-run: every step is idempotent. A failed run can simply be
# re-launched after fixing the reported problem.
# ══════════════════════════════════════════════════════════════════════════

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODDIR="$PROJECT_ROOT/scripts/z_omega_deployment_script"

[[ -d "$MODDIR" ]] || {
  echo "ERROR: missing $MODDIR — this script depends on the modules there. Re-clone/restore the repo." >&2
  exit 1
}

# ---- Load all modules (functions only, no side effects yet) --------------
# shellcheck disable=SC1091
source "$MODDIR/lib_common.sh"
source "$MODDIR/01_detect_os.sh"
source "$MODDIR/02_check_dependencies.sh"
source "$MODDIR/03_check_ports.sh"
source "$MODDIR/04_configure_firewall.sh"
source "$MODDIR/05_configure_env.sh"
source "$MODDIR/06_ssl_setup.sh"
source "$MODDIR/07_backup.sh"
source "$MODDIR/08_maintenance_mode.sh"
source "$MODDIR/09_docker_build_up.sh"
source "$MODDIR/10_smoke_tests.sh"
source "$MODDIR/11_tailscale_setup.sh"

# Once maintenance mode has been armed, if this script exits for ANY reason
# before disable_maintenance_mode runs (a `fail` call, Ctrl-C, a crash), the
# "be right back" page must stay up rather than silently reverting to
# whatever raw error the half-deployed stack would otherwise show. This is
# the safety net for that — it only ever turns things off never on.
_maintenance_exit_trap() {
  local code=$?
  if [[ $code -ne 0 && -f "${MAINTENANCE_FLAG:-/nonexistent}" ]]; then
    echo -e "\n${YELLOW:-}! Maintenance mode is still ON (deploy did not finish cleanly).${RESET:-}"
    echo "    Visitors will keep seeing the 'be right back' page until you either:"
    echo "      - fix the issue above and re-run this script, or"
    echo "      - run: sudo ./scripts/rollback.sh        (also turns it back off), or"
    echo "      - manually clear it: sudo rm -f ${MAINTENANCE_FLAG}"
  fi
}
trap _maintenance_exit_trap EXIT

# ---- Flags -----------------------------------------------------------------
AUTO_YES=false
SKIP_SSL=false
STAGING_SSL=false
SKIP_DEPS=false
FORCE_PORTS=false
FORCE_SSL=false
SKIP_FIREWALL=false
FORCE_DEBUG=false
RECONFIGURE_ENV=false
FORCE_PUBLIC_DOMAIN_SET=false
FORCE_PUBLIC_DOMAIN=""

for arg in "$@"; do
  case "$arg" in
    --yes|-y)        AUTO_YES=true ;;
    --skip-ssl)      SKIP_SSL=true ;;
    --staging-ssl)   STAGING_SSL=true ;;
    --no-deps)       SKIP_DEPS=true ;;
    --force-ports)   FORCE_PORTS=true ;;
    --force-ssl)     FORCE_SSL=true ;;
    --skip-firewall) SKIP_FIREWALL=true ;;
    --debug)         FORCE_DEBUG=true ;;
    --reconfigure-env) RECONFIGURE_ENV=true ;;
    --public-domain)  FORCE_PUBLIC_DOMAIN_SET=true; FORCE_PUBLIC_DOMAIN=true ;;
    --private-domain) FORCE_PUBLIC_DOMAIN_SET=true; FORCE_PUBLIC_DOMAIN=false ;;
    --help|-h)       print_help; exit 0 ;;
    *) echo "Unknown flag: $arg (use --help)"; exit 1 ;;
  esac
done
export AUTO_YES SKIP_SSL STAGING_SSL SKIP_DEPS FORCE_PORTS FORCE_SSL SKIP_FIREWALL FORCE_DEBUG RECONFIGURE_ENV FORCE_PUBLIC_DOMAIN_SET FORCE_PUBLIC_DOMAIN PROJECT_ROOT

# ---- Run the pipeline --------------------------------------------------
banner
detect_os
if [[ "$SKIP_DEPS" == "true" ]]; then
  step "2/10  Skipping dependency install (--no-deps)"
  # Still fix docker-group membership even with --no-deps: this is a
  # permission fix, not a package install, and skipping it is exactly
  # how this bug reappears on a fresh production box.
  ensure_docker_group_membership
else
  check_dependencies
fi
check_ports
configure_firewall
configure_env
setup_ssl
run_pre_deploy_backup
enable_maintenance_mode
docker_build_and_up
# docker_build_and_up already blocked above until docker itself reports both
# 'db' and 'web' healthy (web's healthcheck hits Django's /health/ endpoint
# directly on :8000, bypassing nginx entirely) — that's the real signal the
# app is up, so it's safe to let real traffic through now.
#
# run_smoke_tests below is a SUPPLEMENTARY check, not a gate: two of its
# checks curl https://localhost/ and http://localhost/ THROUGH nginx, which
# would themselves get the maintenance page's 503 for as long as the flag
# stayed up — turning maintenance off first is what lets those checks see
# the real site instead of forever failing against their own maintenance
# page.
disable_maintenance_mode
run_smoke_tests
if [[ "${SMOKE_TEST_FAILURES:-0}" -gt 0 ]]; then
  warn "${SMOKE_TEST_FAILURES} smoke test(s) failed even though the containers reported healthy. The site IS live — but check the warnings above and 'docker compose logs' before calling this deploy fully verified."
fi
setup_tailscale
final_summary
