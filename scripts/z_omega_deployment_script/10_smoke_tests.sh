#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 10_smoke_tests.sh — verify the live stack actually answers
# requests before calling the deploy a success.
# ──────────────────────────────────────────────────────────────

run_smoke_tests() {
  step "10/11  Smoke-testing the running stack"

  local failures=0

  # 1. Internal app health endpoint (bypasses nginx/TLS entirely)
  if docker compose exec -T web python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/')" >/dev/null 2>&1; then
    ok "Django app health endpoint OK (inside 'web' container)."
  else
    warn "Django /health/ endpoint did not respond inside the 'web' container."
    failures=$((failures + 1))
  fi

  # 2. HTTP -> HTTPS redirect via nginx
  local http_code
  http_code=$(curl -sk -o /dev/null -w '%{http_code}' "http://localhost/" || echo "000")
  if [[ "$http_code" == "301" || "$http_code" == "308" ]]; then
    ok "HTTP -> HTTPS redirect working (nginx returned $http_code)."
  else
    warn "Expected a redirect on port 80, got HTTP $http_code."
    failures=$((failures + 1))
  fi

  # 3. HTTPS endpoint (accepts self-signed certs with -k for test mode)
  local https_code
  https_code=$(curl -sk -o /dev/null -w '%{http_code}' "https://localhost/" || echo "000")
  if [[ "$https_code" =~ ^(200|301|302)$ ]]; then
    ok "HTTPS endpoint responding (nginx returned $https_code)."
  else
    warn "HTTPS endpoint returned $https_code."
    failures=$((failures + 1))
  fi

  # 4. Database reachable from web container
  if docker compose exec -T web python manage.py check --database default >/dev/null 2>&1; then
    ok "Database connectivity check passed."
  else
    warn "Database connectivity check failed."
    failures=$((failures + 1))
  fi

  # 5. Static files collected/served
  if curl -sk -o /dev/null -w '%{http_code}' "https://localhost/static/admin/css/base.css" | grep -q "200"; then
    ok "Static files are being served."
  else
    warn "Static file check failed (admin CSS not reachable) — check collectstatic ran."
    failures=$((failures + 1))
  fi

  echo
  if [[ $failures -eq 0 ]]; then
    ok "All smoke tests passed."
  else
    warn "$failures smoke test(s) failed. The stack is up but needs attention — see warnings above and 'docker compose logs' for detail."
  fi

  # Exposed globally (not just returned) so the main script can decide
  # whether it's safe to turn maintenance mode back off — see
  # 08_maintenance_mode.sh / disable_maintenance_mode.
  SMOKE_TEST_FAILURES=$failures
}

final_summary() {
  echo
  echo -e "${BOLD}${GREEN}════════════════════════════════════════════════════════════${RESET}"
  echo -e "${BOLD}${GREEN} Deployment finished${RESET}"
  echo -e "${BOLD}${GREEN}════════════════════════════════════════════════════════════${RESET}"
  info "Domain:        https://${DOMAIN}"
  if [[ "${TAILSCALE_FUNNEL_ACTIVE:-false}" == "true" ]]; then
    info "Mobile access: ${TAILSCALE_URL}  (Tailscale Funnel — stable, survives reboots/updates)"
  elif [[ "${PUBLIC_DOMAIN:-true}" != "true" ]]; then
    warn "Mobile access: Tailscale Funnel was requested but isn't confirmed live — check the warnings in step 11 above."
  fi
  info "Containers:    docker compose ps"
  info "Logs:          docker compose logs -f web"
  info "Deploy log:    logs/deploy_history.log"
  info "Backups:       backups/  (fresh DB + media snapshot taken before this deploy)"
  info "Maintenance:   $([[ -f "${MAINTENANCE_FLAG:-/nonexistent}" ]] && echo "still ON (unexpected — check docker/nginx/maintenance/maintenance.flag)" || echo "off, site is live")"
  info "Rollback:      sudo ./scripts/rollback.sh   (reverts to the pre-deploy image, also clears maintenance mode)"
  info "Firewall:      $([[ "${SKIP_FIREWALL:-false}" == "true" ]] && echo "skipped (--skip-firewall)" || echo "ufw allowing SSH/80/443 only")"
  info "SSL renewal:   automatic (cron + certbot hooks), unless --skip-ssl was used"
  if [[ "${SKIP_SSL:-false}" == "true" || "$DOMAIN" == "localhost" ]]; then
    warn "Running with a SELF-SIGNED cert — this build is for TESTING only."
    warn "For real production, re-run without --skip-ssl on the real domain."
  fi
  if [[ "${DOCKER_GROUP_JUST_ADDED:-false}" == "true" ]]; then
    warn "Added '${SUDO_USER}' to the 'docker' group during this run."
    warn "That only applies to NEW login sessions — this shell won't see it."
    warn "Log out/in (or run: newgrp docker) before expecting 'docker ps'"
    warn "and other docker commands to work for that user WITHOUT sudo."
  fi
  echo
}
