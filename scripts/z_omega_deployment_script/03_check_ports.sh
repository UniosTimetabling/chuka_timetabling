#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 03_check_ports.sh — make sure 80/443 (and 3306 if you keep the
# MySQL port published) are actually free before we try to bind
# them. Fails fast with a clear message rather than letting
# `docker compose up` die confusingly later.
# ──────────────────────────────────────────────────────────────

check_ports() {
  step "3/11  Checking required ports"

  local required_ports=(80 443)
  # 3306 is only a hard requirement if docker-compose.yml still
  # publishes it to the host (it does by default in this repo).
  if grep -qE '^\s*-\s*"(127\.0\.0\.1:)?3306:3306"' "$PROJECT_ROOT/docker-compose.yml" 2>/dev/null; then
    required_ports+=(3306)
  fi
  # 8088 (loopback-only) is nginx's dedicated Tailscale Funnel target —
  # see docker-compose.yml / docker/nginx/default.conf. Always published,
  # regardless of whether Tailscale ends up being used this run.
  if grep -qE '^\s*-\s*"127\.0\.0\.1:8088:8088"' "$PROJECT_ROOT/docker-compose.yml" 2>/dev/null; then
    required_ports+=(8088)
  fi

  local blocked=()
  for port in "${required_ports[@]}"; do
    if port_in_use "$port"; then
      # It's OK if the thing listening is one of OUR OWN containers
      # (e.g. this is a re-run of the deploy).
      local owner
      owner=$(docker ps --format '{{.Names}} {{.Ports}}' 2>/dev/null | grep ":$port->" || true)
      if [[ -n "$owner" ]]; then
        ok "Port $port is in use by our own container ($owner) — fine, will be recreated."
      else
        warn "Port $port is already in use by something else on this host."
        blocked+=("$port")
      fi
    else
      ok "Port $port is free."
    fi
  done

  if [[ ${#blocked[@]} -gt 0 ]]; then
    warn "Blocked ports: ${blocked[*]}"
    info "Find what's using a port with:  sudo ss -ltnp | grep ':<port>'"
    if [[ "${FORCE_PORTS:-false}" == "true" ]]; then
      warn "--force-ports passed — continuing anyway. docker compose up may still fail."
    else
      confirm "Continue anyway despite blocked port(s) ${blocked[*]}?" \
        || fail "Aborting. Free the port(s) above, or re-run with --force-ports to continue anyway."
    fi
  fi
}
