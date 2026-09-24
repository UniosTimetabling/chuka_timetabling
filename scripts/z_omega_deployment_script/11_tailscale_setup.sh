#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 11_tailscale_setup.sh — expose this server to the mobile app over
# Tailscale Funnel when there's no public domain of its own yet.
#
# Only runs when PUBLIC_DOMAIN=false (set in 05_configure_env.sh, from
# the operator's answer to "is this domain already publicly reachable",
# or --public-domain/--private-domain). If the domain IS already
# publicly reachable, this step is skipped — Tailscale has nothing to
# add there and setup_tailscale returns immediately.
#
# IDEMPOTENT BY DESIGN — safe, and expected, to run on every deploy
# ("on updates"): it re-asserts Tailscale is installed, logged in, and
# funnelling the right target every single run. That matters because
# none of the following are one-time guarantees on their own:
#   - a host reboot doesn't automatically bring `tailscale serve`/
#     `funnel` config back on every distro/init-system combination
#   - a Tailscale package self-update can occasionally reset local
#     serve config
#   - a fresh `docker compose up` recreates the nginx container, but
#     tailscaled itself runs on the HOST, not in a container, and only
#     this script re-declares what it should be forwarding to
# Nothing here is destructive if it's already correctly configured —
# every `tailscale` call below is a no-op re-assertion in that case.
#
# CONSISTENT LINK: the public URL is derived from this machine's fixed
# Tailscale device name + tailnet name (both set once, the first time
# `tailscale up` ever runs on this box), NOT from anything that
# changes per-deploy like a container IP, a fresh cert, or a restart
# order. Point the mobile app at it once — it does not change on
# subsequent "on updates" runs of this script, only if the box is
# re-registered under a different device/tailnet name.
# ──────────────────────────────────────────────────────────────

# NOTE ON THE PUBLIC PORT (443 vs 8443):
# docker-compose.yml publishes nginx on 0.0.0.0:443->443. That "0.0.0.0"
# publish makes Docker install an iptables DNAT rule in the nat/PREROUTING
# chain that intercepts ANY inbound packet destined for port 443 on this
# host — including ones arriving over the tailscale0 interface — and
# redirects them straight to the nginx container. That happens upstream of
# tailscaled's own listener, so `tailscale serve/funnel --https=443` looks
# like it configured successfully (no bind-conflict error, because Tailscale
# binds its side differently) but never actually receives a single request:
# Docker's rule wins first. The browser then sees nginx's own self-signed
# cert (CN=timetable.chuka.ac.ke) instead of Tailscale's real cert for the
# .ts.net name, which is why Chrome reports ERR_CERT_AUTHORITY_INVALID.
#
# The fix is to run Funnel's public HTTPS listener on a port Docker never
# touches. 8443 isn't published anywhere in docker-compose.yml, so it's
# safe, and it's one of Tailscale Funnel's officially supported public
# ports (443, 8443, 10000 — https://tailscale.com/kb/1223/funnel). This
# does mean the mobile app / browser URL becomes https://<host>.ts.net:8443
# instead of a bare https://<host>.ts.net — that's expected and is exactly
# what TAILSCALE_URL below will reflect.
TAILSCALE_FUNNEL_PUBLIC_PORT="${TAILSCALE_FUNNEL_PUBLIC_PORT:-8443}"
TAILSCALE_FUNNEL_LOCAL_PORT="${TAILSCALE_FUNNEL_LOCAL_PORT:-8088}"
TAILSCALE_URL=""
TAILSCALE_FUNNEL_ACTIVE=false

setup_tailscale() {
  step "11/11  Tailscale Funnel (mobile access)"

  if [[ "${PUBLIC_DOMAIN:-true}" == "true" ]]; then
    info "Domain is public (or Tailscale was declined in step 5) — skipping Tailscale Funnel."
    info "Mobile devices reach this server directly at https://${DOMAIN}."
    export TAILSCALE_FUNNEL_ACTIVE TAILSCALE_URL
    return 0
  fi

  # TAILSCALE_AUTHKEY/TAILSCALE_HOSTNAME can come from the real shell
  # environment (e.g. `sudo -E ./deploy...`), but most people will just
  # put them in .env like every other secret this script manages —
  # sudo resets the environment by default, so .env is the reliable
  # path. Shell env, if actually present, wins.
  : "${TAILSCALE_AUTHKEY:=$(get_env_var TAILSCALE_AUTHKEY "$PROJECT_ROOT/.env" 2>/dev/null || true)}"
  : "${TAILSCALE_HOSTNAME:=$(get_env_var TAILSCALE_HOSTNAME "$PROJECT_ROOT/.env" 2>/dev/null || true)}"

  cd "$PROJECT_ROOT"

  # ---- install ------------------------------------------------------------
  if ! require_cmd tailscale; then
    if [[ "${PKG_MANAGER_SUPPORTED:-false}" == "true" ]]; then
      info "Installing Tailscale..."
      curl -fsSL https://tailscale.com/install.sh | sh \
        || fail "Tailscale install script failed. Install manually: https://tailscale.com/download, then re-run."
    else
      fail "Tailscale isn't installed, and this distro isn't apt-based so it can't be auto-installed here. Install it yourself (https://tailscale.com/download — most non-apt distros ship a 'tailscale' package, e.g. pacman -S tailscale on Arch) and re-run."
    fi
  fi
  ok "Tailscale present: $(tailscale version 2>/dev/null | head -1)"

  # ---- daemon ---------------------------------------------------------------
  # Same layered approach as ensure_docker_running in lib_common.sh: prefer
  # the init system if there is one, fall back to running the daemon
  # directly otherwise (covers containers, minimal-init IoT images, etc.).
  #
  # IMPORTANT: readiness is checked via `tailscale status --json --peers=false`
  # and just tested for A RESPONSE, never via plain `tailscale status`'s exit
  # code. Plain `tailscale status` exits non-zero whenever the node isn't
  # logged in to a tailnet yet (BackendState=NeedsLogin) — which is the
  # NORMAL state right after a fresh install, before the auth step further
  # below ever runs. Using it here made a perfectly healthy, freshly-started
  # daemon indistinguishable from a dead one, which caused this whole
  # section to conclude tailscaled wasn't running when it actually was, and
  # go on to fight the real daemon for its own socket/state file. The two
  # questions ("is the daemon up" vs. "are we logged in") are handled by
  # separate checks in this function — this one is only about the former.
  tailscaled_reachable() {
    tailscale status --json --peers=false 2>/dev/null | grep -q '"BackendState"'
  }

  if ! tailscaled_reachable; then
    if systemd_available && systemctl list-unit-files 'tailscaled.service' --no-legend 2>/dev/null | grep -q tailscaled.service; then
      info "Starting tailscaled via systemd..."
      if systemctl enable --now tailscaled; then
        # `systemctl enable --now` returning success only means the unit was
        # *asked* to start, not that tailscaled has actually finished
        # initializing yet (TPM capability probing, DNS manager setup, etc.
        # all happen before it opens its control socket) — poll instead of
        # judging success/failure on the very next line.
        local waited_systemd=0
        while (( waited_systemd < 15 )) && ! tailscaled_reachable; do
          sleep 1; waited_systemd=$((waited_systemd + 1))
        done
      else
        warn "systemctl could not start tailscaled — will try running it directly instead."
      fi
    fi
    # If a tailscaled process is genuinely alive (systemd-managed or left
    # over from a previous run) but just slow to answer, wait on THAT rather
    # than racing it with a second process — starting our own here would
    # either crash immediately (real daemon holding the socket) or corrupt
    # shared state (both processes touching the same state file).
    if ! tailscaled_reachable && pgrep -x tailscaled >/dev/null 2>&1; then
      info "A tailscaled process is already running — waiting for it to respond instead of starting a second one..."
      local waited_proc=0
      while (( waited_proc < 15 )) && ! tailscaled_reachable; do
        sleep 1; waited_proc=$((waited_proc + 1))
      done
      # A process that's still not answering after 15s is most likely
      # wedged, not merely slow (a slow-but-fine start is what the systemd
      # wait loop above already covers, and 15s is generous). On a
      # systemd-managed box, `systemctl restart` is the officially
      # supported recovery path — it runs tailscaled's own ExecStopPost
      # cleanup before starting fresh, which is safe here in a way that
      # this script trying to kill/replace the process by hand would not
      # be. This also self-heals a daemon left in a bad state by, e.g., an
      # earlier interrupted run or an unrelated crash.
      if ! tailscaled_reachable && systemd_available && systemctl list-unit-files 'tailscaled.service' --no-legend 2>/dev/null | grep -q tailscaled.service; then
        warn "tailscaled isn't responding — attempting a clean restart via systemd..."
        systemctl restart tailscaled 2>/dev/null || true
        local waited_restart=0
        while (( waited_restart < 15 )) && ! tailscaled_reachable; do
          sleep 1; waited_restart=$((waited_restart + 1))
        done
      fi
    fi
    # Only reachable here once we've confirmed no tailscaled process is
    # actually running — so any socket file still on disk can only be a
    # dead leftover, never a live one, and is safe to clear before we bind
    # our own.
    if ! tailscaled_reachable && ! pgrep -x tailscaled >/dev/null 2>&1; then
      require_cmd tailscaled || fail "tailscaled binary not found after install. Check the Tailscale install above."
      info "Starting tailscaled directly (no systemd unit available/working here)..."
      mkdir -p /var/lib/tailscale
      rm -f /var/run/tailscale/tailscaled.sock
      nohup tailscaled --state=/var/lib/tailscale/tailscaled.state >/tmp/tailscaled.log 2>&1 &
      disown
      local waited=0
      while (( waited < 15 )) && ! tailscaled_reachable; do
        sleep 1; waited=$((waited + 1))
      done
    fi
  fi
  if ! tailscaled_reachable; then
    if pgrep -x tailscaled >/dev/null 2>&1; then
      fail "A tailscaled process is running but its control socket still isn't responding, even after a restart attempt. Its state may be corrupted — check 'sudo systemctl status tailscaled' and 'sudo journalctl -u tailscaled -n 50' for the real error. If it mentions the state file, stopping tailscaled, removing /var/lib/tailscale/tailscaled.state, and starting it again (you'll need to 'tailscale up' and re-authenticate afterwards) usually clears it."
    elif grep -q "address already in use" /tmp/tailscaled.log 2>/dev/null; then
      fail "tailscaled won't respond, and another process already holds /var/run/tailscale/tailscaled.sock. Check what's running it with 'sudo systemctl status tailscaled' and 'sudo ss -lxp | grep tailscaled.sock', then re-run."
    fi
    fail "tailscaled won't respond. Check /tmp/tailscaled.log (or 'systemctl status tailscaled')."
  fi
  ok "tailscaled is running."

  # ---- auth / up ------------------------------------------------------------
  if tailscale status --json 2>/dev/null | grep -q '"BackendState":"Running"'; then
    ok "Already logged in to a tailnet."
  elif [[ -n "${TAILSCALE_AUTHKEY:-}" ]]; then
    info "Authenticating with TAILSCALE_AUTHKEY..."
    tailscale up --authkey="${TAILSCALE_AUTHKEY}" --hostname="${TAILSCALE_HOSTNAME:-${OS_ID:-server}-chuka-timetabling}" --ssh \
      || fail "'tailscale up' failed with the provided TAILSCALE_AUTHKEY. Check it's valid and unused (https://login.tailscale.com/admin/settings/keys)."
  elif [[ -t 0 && "${AUTO_YES:-false}" != "true" ]]; then
    warn "Tailscale is installed but not logged in to a tailnet yet."
    info "Opening 'tailscale up' — follow the printed login URL in a browser to authorize this device."
    tailscale up --hostname="${TAILSCALE_HOSTNAME:-${OS_ID:-server}-chuka-timetabling}" \
      || fail "'tailscale up' did not complete. Re-run once you've authorized this device, or set TAILSCALE_AUTHKEY for a fully non-interactive run."
  else
    fail "Non-interactive run (--yes / no TTY) with no TAILSCALE_AUTHKEY set — can't authenticate to Tailscale unattended. Generate a reusable auth key at https://login.tailscale.com/admin/settings/keys, export TAILSCALE_AUTHKEY=..., and re-run — or run 'sudo tailscale up' by hand once first, interactively."
  fi
  ok "Tailscale is up (IP: $(tailscale ip -4 2>/dev/null | head -1))."

  # ---- serve + funnel (idempotent — safe to re-declare every run) ---------
  # Point Tailscale's HTTPS edge at nginx's dedicated Tailscale-only vhost
  # (docker/nginx/default.conf, `listen 8088`, published as
  # 127.0.0.1:8088 in docker-compose.yml) rather than nginx's normal :443.
  # That vhost has no HTTP->HTTPS redirect to loop against and doesn't
  # care about nginx's self-signed/Let's Encrypt cert, since Tailscale
  # itself terminates real TLS for *.ts.net at its own edge.
  # Clean up a stale rule on :443 from before this script switched to
  # :8443 (Docker's 0.0.0.0:443 publish shadows tailscaled on that port —
  # see the note above TAILSCALE_FUNNEL_PUBLIC_PORT). `serve`/`funnel off`
  # on a port with no existing rule is a harmless no-op, so this is safe
  # to run unconditionally on every deploy.
  if [[ "$TAILSCALE_FUNNEL_PUBLIC_PORT" != "443" ]]; then
    tailscale funnel --https=443 off >/dev/null 2>&1 || true
    tailscale serve --https=443 off >/dev/null 2>&1 || true
  fi

  info "Configuring 'tailscale serve' -> 127.0.0.1:${TAILSCALE_FUNNEL_LOCAL_PORT} ..."
  tailscale serve --bg --https="${TAILSCALE_FUNNEL_PUBLIC_PORT}" "http://127.0.0.1:${TAILSCALE_FUNNEL_LOCAL_PORT}" \
    || fail "'tailscale serve' failed to configure the local proxy target. If the output above says 'Serve is not enabled on your tailnet', that's an account-level setting an admin must turn on first at the URL Tailscale printed (or https://login.tailscale.com/f/serve) — this isn't something this script or box can enable on its own. Re-run once it's on."

  # IMPORTANT: `tailscale funnel <target>` takes a TARGET, not a "make this
  # port public" toggle — a bare port number like `443` is shorthand for
  # "expose local port 443" (https://tailscale.com/kb/1311/funnel-cli),
  # NOT "flip the existing --https=443 serve rule above to public". Passing
  # just `443` here silently replaces the http://127.0.0.1:8088 target
  # configured by `serve` above with a brand new one pointing at
  # 127.0.0.1:443 — nginx's own HTTPS port, which serves nginx's
  # self-signed/Let's-Encrypt cert directly instead of going through
  # Tailscale's own TLS termination. That double/wrong TLS layer is exactly
  # what showed up as ERR_CERT_AUTHORITY_INVALID in browsers hitting the
  # funnel URL. The fix is to give `funnel` the SAME target as `serve` —
  # Tailscale's own model is "most recent command (serve vs funnel) on a
  # given port wins", so repeating the identical --https target here
  # correctly promotes the existing rule to public instead of replacing it.
  #
  # We use TAILSCALE_FUNNEL_PUBLIC_PORT (8443, not 443) as that shared
  # target port — see the note above the variable's declaration for why
  # plain 443 doesn't work on this box (Docker's 0.0.0.0:443 publish for
  # nginx shadows it).
  info "Enabling public Funnel on :${TAILSCALE_FUNNEL_PUBLIC_PORT} ..."
  tailscale funnel --bg --https="${TAILSCALE_FUNNEL_PUBLIC_PORT}" "http://127.0.0.1:${TAILSCALE_FUNNEL_LOCAL_PORT}" \
    || fail "'tailscale funnel' failed to enable the public funnel. Funnel must be turned on for this tailnet/node by an admin first: https://tailscale.com/kb/1223/funnel"

  # ---- discover the stable public URL ---------------------------------------
  # Match our specific port so a lingering rule on another port (e.g. an
  # old :443 entry that failed to clear) can't be picked up instead.
  TAILSCALE_URL=$(tailscale funnel status 2>/dev/null \
    | grep -oE "https://[a-zA-Z0-9.-]+\.ts\.net(:${TAILSCALE_FUNNEL_PUBLIC_PORT})?" | head -1)
  if [[ -z "$TAILSCALE_URL" ]]; then
    local dns_name
    dns_name=$(tailscale status --json 2>/dev/null \
      | python3 -c 'import json,sys; print(json.load(sys.stdin).get("Self",{}).get("DNSName","").rstrip("."))' 2>/dev/null || true)
    [[ -n "$dns_name" ]] && TAILSCALE_URL="https://${dns_name}"
  fi
  # `funnel status` omits the port suffix for the default :443 case but
  # includes it for anything else — make sure it's actually present so
  # the URL we print/save is reachable rather than silently missing :8443.
  if [[ -n "$TAILSCALE_URL" && "$TAILSCALE_FUNNEL_PUBLIC_PORT" != "443" \
        && "$TAILSCALE_URL" != *":${TAILSCALE_FUNNEL_PUBLIC_PORT}" ]]; then
    TAILSCALE_URL="${TAILSCALE_URL}:${TAILSCALE_FUNNEL_PUBLIC_PORT}"
  fi

  if [[ -z "$TAILSCALE_URL" ]]; then
    warn "Funnel was configured but the public URL couldn't be determined automatically."
    warn "Run 'tailscale funnel status' on this box to find it."
    TAILSCALE_FUNNEL_ACTIVE=false
    export TAILSCALE_FUNNEL_ACTIVE TAILSCALE_URL
    return 0
  fi

  ok "Tailscale Funnel is live: ${TAILSCALE_URL}"
  echo "$TAILSCALE_URL" > "$PROJECT_ROOT/.tailscale_url"
  set_env_var "TAILSCALE_FUNNEL_URL" "$TAILSCALE_URL" "$PROJECT_ROOT/.env"

  # ---- make Django actually accept requests through this hostname --------
  # Without these, Funnel would work at the network level but every
  # mobile request would still 400 (DisallowedHost) or fail CSRF once it
  # reached Django.
  # ALLOWED_HOSTS is matched against the incoming Host header with any
  # port stripped off first (Django does this internally), so an entry
  # that still has ":8443" on it would just never match — strip it here.
  # CORS_ALLOWED_ORIGINS / CSRF_TRUSTED_ORIGINS are matched against the
  # full Origin header instead, which DOES include the port, so those two
  # keep using $TAILSCALE_URL (with the port) as-is further down.
  # ALLOWED_HOSTS is matched against the incoming Host header with any
  # port stripped off first (Django does this internally), so an entry
  # that still has ":8443" on it would just never match — strip it here.
  # CORS_ALLOWED_ORIGINS / CSRF_TRUSTED_ORIGINS are matched against the
  # full Origin header instead, which DOES include the port, so those two
  # keep using $TAILSCALE_URL (with the port) as-is further down.
  local ts_host="${TAILSCALE_URL#https://}"
  ts_host="${ts_host%%:*}"
  local before_hosts before_cors before_csrf after_hosts after_cors after_csrf env_changed=false
  before_hosts=$(get_env_var "ALLOWED_HOSTS" "$PROJECT_ROOT/.env")
  before_cors=$(get_env_var "CORS_ALLOWED_ORIGINS" "$PROJECT_ROOT/.env")
  before_csrf=$(get_env_var "CSRF_TRUSTED_ORIGINS" "$PROJECT_ROOT/.env")

  # merge_env_csv_list (lib_common.sh) additively merges rather than
  # overwrites, and includes the SAME local-dev defaults settings.py
  # itself falls back to (127.0.0.1,localhost / http://localhost:8081,
  # http://127.0.0.1:8081). That fallback only applies while the .env key
  # is completely absent — the first time anything writes one of these
  # keys, Django/django-cors-headers use exactly what's in .env from then
  # on. Passing the same literal defaults through the merge here is a
  # no-op for runtime behavior but makes them explicit in .env, so this
  # (or a later step, on any box) can never silently drop local-dev
  # access by writing just the newly-discovered Tailscale entry alone.
  after_hosts=$(merge_env_csv_list "ALLOWED_HOSTS" "127.0.0.1,localhost,${ts_host}" "$PROJECT_ROOT/.env")
  after_cors=$(merge_env_csv_list "CORS_ALLOWED_ORIGINS" "http://localhost:8081,http://127.0.0.1:8081,${TAILSCALE_URL}" "$PROJECT_ROOT/.env")
  after_csrf=$(merge_env_csv_list "CSRF_TRUSTED_ORIGINS" "${TAILSCALE_URL}" "$PROJECT_ROOT/.env")

  if [[ "$after_hosts" != "$before_hosts" || "$after_cors" != "$before_cors" || "$after_csrf" != "$before_csrf" ]]; then
    env_changed=true
  fi

  if $env_changed; then
    if docker compose ps -q web >/dev/null 2>&1 && [[ -n "$(docker compose ps -q web 2>/dev/null)" ]]; then
      info "Restarting 'web' so it picks up the Tailscale hostname (ALLOWED_HOSTS/CORS/CSRF)..."
      docker compose up -d --no-deps web >/dev/null 2>&1 \
        || warn "Could not restart 'web' automatically — run 'docker compose up -d web' by hand so the .env changes above take effect."
    else
      info "'web' isn't running yet — it will pick up these .env values on its next start."
    fi
  else
    ok "ALLOWED_HOSTS/CORS/CSRF already include ${ts_host} — nothing to restart."
  fi

  TAILSCALE_FUNNEL_ACTIVE=true
  export TAILSCALE_URL TAILSCALE_FUNNEL_ACTIVE
}
