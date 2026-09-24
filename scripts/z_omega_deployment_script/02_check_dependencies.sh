#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 02_check_dependencies.sh — verify/install everything the deploy
# needs: docker, docker compose plugin, curl, openssl, certbot.
# ──────────────────────────────────────────────────────────────

check_dependencies() {
  step "2/11  Checking dependencies"

  if [[ "${PKG_MANAGER_SUPPORTED:-false}" != "true" ]]; then
    warn "Skipping automated dependency install — this distro isn't apt-based."
    warn "Make sure docker, the docker compose plugin, curl, openssl, and (unless"
    warn "--skip-ssl) certbot are already installed via this distro's own package"
    warn "manager before continuing. The rest of the deploy (steps 3-10) is fully"
    warn "distro-agnostic and treats this box as production like any other."
    for cmd in docker curl openssl; do
      require_cmd "$cmd" || fail "'$cmd' not found and can't be auto-installed on this distro. Install it manually and re-run."
    done
    docker compose version >/dev/null 2>&1 || fail "docker compose plugin not found and can't be auto-installed on this distro. Install it manually and re-run."
    if [[ "${SKIP_SSL:-false}" != "true" ]]; then
      require_cmd certbot || fail "'certbot' not found and can't be auto-installed on this distro. Install it manually, or pass --skip-ssl for a self-signed cert."
    fi
    ensure_docker_running
    ensure_docker_group_membership
    return 0
  fi

  apt_update_safe

  # ---- curl / openssl ----------------------------------------------------
  for pkg_cmd in "curl:curl" "openssl:openssl" "ca-certificates:update-ca-certificates"; do
    pkg="${pkg_cmd%%:*}"; cmd="${pkg_cmd##*:}"
    if ! require_cmd "$cmd"; then
      info "Installing $pkg..."
      apt-get install -y -qq "$pkg"
    fi
  done
  ok "curl / openssl present."

  # ---- Docker + Compose plugin -------------------------------------------
  # Check the CLI *and* the engine — a box can have `docker` on PATH
  # (e.g. just docker-ce-cli, or a client-only package) with no dockerd
  # at all, which would otherwise sail past this check and only surface
  # as a confusing failure two steps later when we try to start it.
  if ! require_cmd docker || ! require_cmd dockerd; then
    if require_cmd docker && ! require_cmd dockerd; then
      warn "Docker CLI is present but the engine (dockerd) is not — this is"
      warn "a client-only install. The full engine is required to build/run"
      warn "the stack, not just talk to a remote one."
    else
      warn "Docker is not installed."
    fi
    confirm "Install Docker Engine now?" || fail "Docker Engine (dockerd) is required. Install it manually then re-run."
    info "Installing Docker..."

    # Prefer the distro's OWN docker.io package. It's built against
    # whatever apt repo this box already has configured, so it works
    # regardless of codename. get.docker.com, by contrast, hardcodes
    # Docker's own upstream repo, which only lists a handful of real
    # Debian/Ubuntu release codenames — it has no entry for rolling/
    # testing-style codenames (e.g. Kali's kali-rolling), so it fails
    # with "does not have a Release file" on those every time,
    # regardless of network conditions or retries.
    if apt-cache show docker.io >/dev/null 2>&1; then
      info "Installing docker.io from this system's own apt repos..."
      apt-get install -y -qq docker.io || warn "apt install of docker.io failed — falling back to get.docker.com."
    fi

    if ! require_cmd dockerd; then
      info "docker.io unavailable or failed — trying get.docker.com instead"
      info "(this only works on codenames Docker's own repo publishes for,"
      info "e.g. current Debian/Ubuntu releases — not rolling distros)."
      curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
      if ! sh /tmp/get-docker.sh; then
        warn "get.docker.com failed — cleaning up its apt repo entry so it"
        warn "doesn't break 'apt-get update' on the next run."
        rm -f /etc/apt/sources.list.d/docker.list
        apt-get update -y -qq || true
      fi
      rm -f /tmp/get-docker.sh
    fi

    require_cmd dockerd || fail "Could not install Docker Engine via either docker.io (apt) or get.docker.com. On a rolling/non-standard-codename distro, install docker.io manually via apt, or check your distro's own documented Docker install path."
  fi
  ok "Docker present: $(docker --version)"

  if ! docker compose version >/dev/null 2>&1; then
    warn "Docker Compose plugin missing."
    confirm "Install docker-compose-plugin via apt now?" || fail "docker compose plugin is required."
    apt-get install -y -qq docker-compose-plugin || fail "docker-compose-plugin isn't available via apt on this system. Install the Compose plugin manually (see https://docs.docker.com/compose/install/) and re-run."
  fi
  ok "Docker Compose present: $(docker compose version --short 2>/dev/null || docker compose version)"

  ensure_docker_running
  ensure_docker_group_membership

  # ---- certbot (only needed for real SSL, not --skip-ssl) ----------------
  if [[ "${SKIP_SSL:-false}" != "true" ]]; then
    if ! require_cmd certbot; then
      info "Installing certbot (needed for Let's Encrypt)..."
      apt-get install -y -qq certbot || fail "certbot install failed via apt. Install it manually (see https://certbot.eff.org/instructions), or pass --skip-ssl for a self-signed test cert instead."
    fi
    ok "certbot present: $(certbot --version 2>&1)"
  else
    info "Skipping certbot check (--skip-ssl passed)."
  fi

  # ---- git (optional, only if this deploy pulls updates) -----------------
  if ! require_cmd git; then
    apt-get install -y -qq git || warn "git install failed (non-fatal — only needed if you pull updates via git)."
  fi
}
