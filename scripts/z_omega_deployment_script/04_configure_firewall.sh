#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 04_configure_firewall.sh — basic host hardening with ufw.
#
# Always allows SSH FIRST, before ever enabling ufw, to avoid
# locking out the very session running this script. Then allows
# HTTP/HTTPS only. Everything else stays denied by default.
#
# Safe on managed cloud hosts too: if ufw isn't installable, or
# the host is already firewalled at the network/security-group
# level, this step warns and continues rather than failing the
# whole deploy over it. Pass --skip-firewall to bypass entirely.
# ──────────────────────────────────────────────────────────────

configure_firewall() {
  step "4/11  Configuring host firewall (ufw)"

  if [[ "${SKIP_FIREWALL:-false}" == "true" ]]; then
    info "Skipping firewall configuration (--skip-firewall passed)."
    return
  fi

  if in_container; then
    warn "Running inside a container — ufw/iptables generally can't manage"
    warn "the netfilter tables from in here (that belongs to the host's"
    warn "kernel). Skipping firewall configuration."
    warn "If this is a real deploy (not a container test rig), make sure"
    warn "the HOST firewall or cloud security group allows SSH/80/443."
    return
  fi

  if ! require_cmd ufw; then
    if [[ "${PKG_MANAGER_SUPPORTED:-false}" == "true" ]]; then
      info "ufw not found — attempting to install via apt..."
      if ! apt-get install -y -qq ufw; then
        warn "Could not install ufw (may be unavailable in this environment, e.g. some containers/WSL)."
        warn "If this host sits behind a cloud security group, that may already cover this — continuing."
        return
      fi
    else
      warn "ufw not found and this distro isn't apt-based, so it can't be auto-installed here."
      warn "Install this distro's firewall tooling yourself (e.g. 'pacman -S ufw' on Arch, or use"
      warn "firewalld/nftables directly) to get the same SSH/80/443 hardening, or pass --skip-firewall"
      warn "if this host is already firewalled elsewhere (cloud security group, upstream router, etc.)."
      return
    fi
  fi

  # ---- Figure out the real SSH port BEFORE touching anything -------------
  local ssh_port=22
  if [[ -f /etc/ssh/sshd_config ]]; then
    local configured_port
    configured_port=$(grep -E '^\s*Port\s+[0-9]+' /etc/ssh/sshd_config 2>/dev/null | awk '{print $2}' | tail -1 || true)
    [[ -n "$configured_port" ]] && ssh_port="$configured_port"
  fi

  info "Detected SSH port: $ssh_port (this is allowed first, before ufw is ever enabled)."

  # ---- Rules are idempotent: safe to re-run ------------------------------
  # These are allowed to fail soft (rather than aborting the whole deploy
  # under set -e) because some sandboxes/WSL setups pass the in_container
  # check above yet still can't reach netfilter — e.g. "modprobe: not found"
  # or "Could not load module" from ufw/iptables.
  if ! {
    ufw allow "${ssh_port}/tcp" comment "SSH" >/dev/null &&
    ufw allow 80/tcp  comment "HTTP (ACME + redirect)" >/dev/null &&
    ufw allow 443/tcp comment "HTTPS" >/dev/null &&
    ufw allow 41641/udp comment "Tailscale (optional, harmless if unused)" >/dev/null
  }; then
    warn "ufw could not add its rules (likely no netfilter access in this"
    warn "environment). Skipping firewall configuration — make sure the"
    warn "host/network firewall covers SSH/80/443 instead."
    return
  fi
  ok "Allowed: ${ssh_port}/tcp (SSH), 80/tcp, 443/tcp, 41641/udp (Tailscale)"

  if ufw status | grep -q "Status: active"; then
    ok "ufw already active — rules refreshed."
    return
  fi

  warn "ufw is currently INACTIVE. Enabling it will block every port except"
  warn "${ssh_port} (SSH), 80, 443, and 41641/udp (Tailscale) — double-check ${ssh_port} really"
  warn "is your SSH port before continuing, or you could lose remote access to this box."

  if confirm "Enable ufw now with these rules?"; then
    ufw --force enable
    ok "ufw enabled."
    ufw status verbose
  else
    warn "Skipped enabling ufw. Rules are staged but NOT active — run 'sudo ufw enable' yourself later."
  fi
}
