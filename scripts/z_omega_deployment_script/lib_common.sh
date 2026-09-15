#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# lib_common.sh — shared helpers for deploy_production_webserver.sh
# Sourced by every module in scripts/z_omega_deployment_script/.
# Defines functions only — has no side effects when sourced.
# ──────────────────────────────────────────────────────────────

BOLD="$(tput bold 2>/dev/null || true)"
GREEN="$(tput setaf 2 2>/dev/null || true)"
YELLOW="$(tput setaf 3 2>/dev/null || true)"
RED="$(tput setaf 1 2>/dev/null || true)"
CYAN="$(tput setaf 6 2>/dev/null || true)"
RESET="$(tput sgr0 2>/dev/null || true)"

step()  { echo -e "\n${BOLD}${CYAN}==>${RESET} ${BOLD}$1${RESET}"; }
info()  { echo -e "    $1"; }
ok()    { echo -e "    ${GREEN}✓${RESET} $1"; }
warn()  { echo -e "    ${YELLOW}! $1${RESET}"; }
fail()  { echo -e "${RED}✗ ERROR:${RESET} $1" >&2; exit 1; }

# confirm "question text" -> returns 0 (yes) or 1 (no)
# Honors global AUTO_YES=true to skip prompting entirely.
confirm() {
  local prompt="$1"
  if [[ "${AUTO_YES:-false}" == "true" ]]; then
    info "(auto-confirmed: $prompt) [--yes]"
    return 0
  fi
  if [[ ! -t 0 ]]; then
    fail "Not an interactive terminal and --yes was not passed. Refusing to guess on: $prompt"
  fi
  local reply
  read -r -p "    ${BOLD}${prompt}${RESET} [y/N] " reply
  [[ "$reply" =~ ^[Yy]([Ee][Ss])?$ ]]
}

# ask "Prompt text" "default_value" -> echoes the chosen value
ask() {
  local prompt="$1" default="${2:-}" reply
  if [[ "${AUTO_YES:-false}" == "true" ]]; then
    echo "$default"
    return
  fi
  if [[ -n "$default" ]]; then
    read -r -p "    ${prompt} [${default}]: " reply
    echo "${reply:-$default}"
  else
    read -r -p "    ${prompt}: " reply
    echo "$reply"
  fi
}

# ask_secret "Prompt text" -> echoes the entered value WITHOUT echoing
# it back to the terminal (uses `read -s`). Returns an empty string if
# there's no human here to type one (AUTO_YES / no TTY) so callers can
# fall back to auto-generation instead of hanging.
ask_secret() {
  local prompt="$1" reply
  if [[ "${AUTO_YES:-false}" == "true" || ! -t 0 ]]; then
    echo ""
    return
  fi
  read -rs -p "    ${prompt}: " reply
  echo >&2   # `read -s` eats the Enter keypress — print the newline
             # ourselves, to stderr so it doesn't end up in the captured value.
  echo "$reply"
}

# password_strength_issues "candidate" -> prints one problem per line
# (empty output = password is strong enough). Mirrors a standard
# production password policy: length + character-class diversity, and
# blocks a short list of obviously-bad placeholder values so nobody
# ships "changeme"-style passwords as the actual admin login.
password_strength_issues() {
  local pass="$1" len=${#1} lower_pass bad
  local -a issues=()

  (( len < 12 )) && issues+=("at least 12 characters long (got $len)")
  [[ "$pass" =~ [a-z] ]]        || issues+=("a lowercase letter")
  [[ "$pass" =~ [A-Z] ]]        || issues+=("an uppercase letter")
  [[ "$pass" =~ [0-9] ]]        || issues+=("a digit")
  [[ "$pass" =~ [^a-zA-Z0-9] ]] || issues+=("a special character (e.g. !@#%^&*-_=+)")

  lower_pass="${pass,,}"
  for bad in changeme password admin123 letmein admin qwerty 12345678 iloveyou; do
    [[ "$lower_pass" == "$bad" ]] && issues+=("not be a common/placeholder password like '$bad'")
  done

  (( ${#issues[@]} > 0 )) && printf '%s\n' "${issues[@]}"
  return 0
}

# mask "secretvalue" -> shows first 2 chars + asterisks, for safe display
mask() {
  local v="$1" n=${#1}
  [[ -z "$v" ]] && { echo "(blank)"; return; }
  if [[ $n -le 4 ]]; then echo "****"; else echo "${v:0:2}$(printf '*%.0s' $(seq 1 $((n-2))))"; fi
}

# set_env_var KEY VALUE FILE  — idempotent, adds the line if missing
set_env_var() {
  local key="$1" value="$2" file="$3"
  # escape sed metacharacters in value
  local esc_value
  esc_value=$(printf '%s' "$value" | sed -e 's/[\/&]/\\&/g')
  if grep -qE "^${key}=" "$file" 2>/dev/null; then
    sed -i "s/^${key}=.*/${key}=${esc_value}/" "$file"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

# get_env_var KEY FILE — echoes current value or blank
get_env_var() {
  local key="$1" file="$2"
  grep -E "^${key}=" "$file" 2>/dev/null | head -1 | cut -d'=' -f2-
}

# merge_env_csv_list KEY NEW_CSV FILE — additively merges NEW_CSV's
# comma-separated values into KEY's existing comma-separated value in
# FILE, skipping any value already present, and leaving the existing
# order/entries otherwise untouched. Returns (echoes) the resulting
# merged value so callers can detect whether anything actually changed.
#
# This exists because several settings (ALLOWED_HOSTS, CORS_ALLOWED_
# ORIGINS, CSRF_TRUSTED_ORIGINS) get contributed to by more than one
# deploy step (the production domain in 05_configure_env.sh, the
# Tailscale Funnel hostname in 11_tailscale_setup.sh) and can also carry
# entries a human added by hand (a debugging tunnel hostname, a second
# dev machine's localhost port). A plain `set_env_var` on any one of
# those keys blows away everything the OTHER contributors already put
# there — that happened for real here (a fresh domain-config run wiping
# a previously-added Tailscale hostname, and CORS_ALLOWED_ORIGINS never
# picking up the production domain at all since nothing ever merged into
# it). Every deploy step that touches one of these three keys should use
# this helper instead of set_env_var directly, on ANY box/deployment —
# the failure mode isn't specific to any one hostname or tailnet.
merge_env_csv_list() {
  local key="$1" new_csv="$2" file="$3"
  local merged item
  merged=$(get_env_var "$key" "$file")
  local IFS=','
  for item in $new_csv; do
    [[ -z "$item" ]] && continue
    if [[ ",${merged}," != *",${item},"* ]]; then
      merged="${merged:+${merged},}${item}"
    fi
  done
  set_env_var "$key" "$merged" "$file"
  echo "$merged"
}

generate_secret_key() {
  # NOTE: deliberately excludes '$' — docker compose performs variable
  # interpolation on .env file contents themselves, so a '$' followed by
  # alphanumerics in a generated secret gets silently read as a reference
  # to a (usually nonexistent) variable and blanked out, corrupting the
  # secret. Backtick and backslash are excluded for the same class of
  # reason (shell/quoting hazards wherever this value gets sourced).
  python3 -c "import secrets,string; a=string.ascii_letters+string.digits+'!@#%^&*-_=+'; print(''.join(secrets.choice(a) for _ in range(50)))" 2>/dev/null \
    || openssl rand -base64 48 | tr -d '\n=$' | cut -c1-50
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1
}

# apt_update_safe -> like `apt-get update`, but recovers automatically if
# a previously-added Docker apt repo (e.g. from a get.docker.com run that
# failed because this codename isn't in Docker's own repo, such as
# rolling-release apt-based distros like Kali) is now broken. Without this,
# a plain `apt-get update` failing here would abort the entire deploy under
# `set -e` on every subsequent run, even after the real Docker issue is
# otherwise fixed. Only called on apt-based distros — see
# PKG_MANAGER_SUPPORTED in 01_detect_os.sh.
apt_update_safe() {
  if apt-get update -y -qq 2>/tmp/apt-update-err.log; then
    return 0
  fi

  if [[ -f /etc/apt/sources.list.d/docker.list ]] && grep -q 'download.docker.com' /tmp/apt-update-err.log; then
    warn "Removing a broken Docker apt repo entry (left over from a prior"
    warn "get.docker.com attempt that doesn't support this codename)..."
    rm -f /etc/apt/sources.list.d/docker.list
    apt-get update -y -qq && return 0
  fi

  fail "apt-get update failed:\n$(tail -5 /tmp/apt-update-err.log)"
}

# ──────────────────────────────────────────────────────────────
# Container / init-system detection.
#
# Never assume "systemd manages this host's Docker" — on many
# production boxes that's true, but this same script also gets run
# inside Docker-in-Docker test rigs, WSL, IoT/edge boxes with minimal
# init systems, and other environments where PID 1 is not systemd and
# there is no docker.service unit to enable. Layer several signals so
# one false negative doesn't sink the check:
#   - /.dockerenv                      (Docker sets this file)
#   - /proc/1/cgroup mentioning docker/containerd/kubepods
#   - systemd-detect-virt --container  (most authoritative when present)
# ──────────────────────────────────────────────────────────────
in_container() {
  [[ -f /.dockerenv ]] && return 0

  if [[ -r /proc/1/cgroup ]] && grep -qE 'docker|containerd|kubepods|lxc' /proc/1/cgroup 2>/dev/null; then
    return 0
  fi

  if require_cmd systemd-detect-virt; then
    local virt
    virt=$(systemd-detect-virt --container 2>/dev/null || true)
    [[ -n "$virt" && "$virt" != "none" ]] && return 0
  fi

  return 1
}

# systemd_available -> true if systemd is actually running as the init
# system on this host. Prefer systemd-detect-virt/PID 1 comm over
# `systemctl is-system-running`, since that command can report a
# transient state (or behave oddly under some sudo/PAM setups) even
# when systemd is genuinely PID 1 — a false negative here sends us
# down the wrong fallback path entirely.
systemd_available() {
  require_cmd systemctl || return 1
  # The most direct signal: what is PID 1 actually running?
  if [[ -r /proc/1/comm ]] && grep -q '^systemd$' /proc/1/comm 2>/dev/null; then
    return 0
  fi
  [[ -d /run/systemd/system ]]
}

# docker_unit_exists -> true only if systemd actually has a docker.service
# unit registered. Distinct from systemd_available: a host can have a
# perfectly healthy systemd with no docker.service at all (e.g. Docker
# installed via a method that skipped the unit file, or the unit got
# removed/masked) — that's a packaging problem, not an init-detection one,
# and needs its own fallback rather than a confusing systemctl error.
docker_unit_exists() {
  systemctl list-unit-files 'docker.service' --no-legend 2>/dev/null | grep -q 'docker.service'
}

# start_dockerd_directly -> launch the daemon ourselves, bypassing any
# init system. Used both inside containers and as the fallback when a
# host has systemd but no docker.service unit to hand off to.
start_dockerd_directly() {
  require_cmd dockerd || return 1
  info "Starting dockerd directly (bypassing init system)..."
  nohup dockerd >/tmp/dockerd.log 2>&1 &
  disown
  local waited=0
  while (( waited < 30 )) && ! docker info >/dev/null 2>&1; do
    sleep 1; waited=$((waited + 1))
  done
  docker info >/dev/null 2>&1
}

# ensure_docker_running -> makes a best effort to get `docker` talking
# to a live daemon, using whatever init system actually exists, and
# fails with a clear, actionable message instead of letting a raw
# systemctl error take down the whole `set -e` pipeline.
ensure_docker_running() {
  if docker info >/dev/null 2>&1; then
    ok "Docker daemon is running."
    return 0
  fi

  if in_container; then
    warn "Running inside a container — this container has no init system of"
    warn "its own managing Docker (no docker.service unit here)."

    if [[ -S /var/run/docker.sock ]]; then
      fail "Docker socket exists at /var/run/docker.sock but 'docker info' still failed. Check that it's mounted from the host with the right permissions (-v /var/run/docker.sock:/var/run/docker.sock)."
    fi

    if start_dockerd_directly; then
      ok "dockerd started directly (container has no systemd)."
      return 0
    fi
    fail "Could not start dockerd inside this container. See /tmp/dockerd.log. For true Docker-in-Docker you likely need the 'docker:dind' image and --privileged, or to mount the host's /var/run/docker.sock instead."
  fi

  if systemd_available && docker_unit_exists; then
    info "Starting Docker daemon via systemd..."
    systemctl enable --now docker || fail "systemctl could not start docker.service — check 'systemctl status docker' for details."
    ok "Docker daemon is running."
    return 0
  fi

  if systemd_available && ! docker_unit_exists; then
    warn "systemd is running on this host, but no docker.service unit is"
    warn "registered — Docker was likely installed by a method that doesn't"
    warn "ship the systemd unit (e.g. a bare 'dockerd' binary, or a package"
    warn "whose postinst skipped service registration)."
  fi

  # Either no (working) systemd on this host, or systemd exists but the
  # unit doesn't — either way, try running dockerd ourselves before
  # giving up.
  if start_dockerd_directly; then
    ok "dockerd started directly."
    return 0
  fi

  if ! systemd_available && require_cmd service; then
    info "No systemd detected — starting Docker via 'service docker start'..."
    service docker start || fail "'service docker start' failed. Start Docker manually for this init system and re-run."
    sleep 2
    docker info >/dev/null 2>&1 || fail "Docker still isn't responding after 'service docker start'. Check its logs and re-run."
    ok "Docker daemon is running."
    return 0
  fi

  fail "Docker isn't running and no supported way to start it was found (no docker.service unit, no dockerd binary to run directly, no 'service' fallback). Check 'systemctl status docker' / reinstall Docker and re-run."
}

# ──────────────────────────────────────────────────────────────
# ensure_docker_group_membership -> makes sure the human operator who
# invoked this script (via sudo) can run `docker` afterwards WITHOUT
# sudo. This script itself always talks to Docker as root, so a missing
# docker-group membership never breaks the deploy — it only bites the
# operator afterwards, e.g.:
#   "permission denied while trying to connect to the Docker daemon
#    socket at unix:///var/run/docker.sock"
# Fix it here, once, instead of relying on everyone remembering the
# manual `usermod -aG docker $USER` step (and forgetting it on a fresh
# test box, then hitting the exact same issue on production).
# Sets DOCKER_GROUP_JUST_ADDED=true so final_summary can remind the
# operator that a re-login is still required — group changes never
# apply to an already-running shell.
# ──────────────────────────────────────────────────────────────
DOCKER_GROUP_JUST_ADDED=false
ensure_docker_group_membership() {
  # Only meaningful if this was actually invoked via sudo by a real,
  # non-root user — if run directly as root (no SUDO_USER), there's no
  # separate operator account to fix up, so skip silently.
  local target_user="${SUDO_USER:-}"
  if [[ -z "$target_user" || "$target_user" == "root" ]]; then
    return 0
  fi

  if ! getent group docker >/dev/null 2>&1; then
    warn "No 'docker' group exists yet on this system — skipping membership"
    warn "check (it's normally created automatically by the Docker install)."
    return 0
  fi

  if id -nG "$target_user" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
    ok "User '$target_user' is already in the 'docker' group."
    return 0
  fi

  info "User '$target_user' is not in the 'docker' group yet — this is why"
  info "'docker ps' (without sudo) fails with 'permission denied' on the"
  info "socket. Adding them now so this doesn't repeat on production."
  if usermod -aG docker "$target_user"; then
    ok "Added '$target_user' to the 'docker' group."
    DOCKER_GROUP_JUST_ADDED=true
  else
    warn "Could not add '$target_user' to the docker group automatically."
    warn "Run manually:  sudo usermod -aG docker $target_user"
  fi
}

port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "( sport = :$port )" 2>/dev/null | grep -q ":$port"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null && { exec 3>&- 3<&-; return 0; } || return 1
  fi
}

banner() {
  echo -e "${BOLD}${CYAN}"
  echo "════════════════════════════════════════════════════════════"
  echo "  Chuka University Timetabling System — Production Deployer"
  echo "════════════════════════════════════════════════════════════"
  echo -e "${RESET}"
}

print_help() {
  cat <<HELP
Usage: sudo ./deploy_production_webserver.sh [options]

Options:
  --yes, -y        Non-interactive. Accept all defaults / confirmations.
  --skip-ssl        Skip Let's Encrypt entirely; use a self-signed cert
                     (for local/test environments, or any box with no
                     public DNS name yet — e.g. a Kali VM or an IoT device
                     on a LAN).
  --staging-ssl     Request a Let's Encrypt STAGING cert (safe for
                     repeated testing, not trusted by browsers).
  --force-ssl       Re-issue/regenerate certs even if valid ones exist.
  --no-deps         Skip the OS package / Docker install step.
  --force-ports     Continue even if required ports look occupied.
  --skip-firewall   Don't touch ufw (e.g. firewall already managed by a
                     cloud security group, or ufw isn't available here).
  --debug           Force DEBUG=True in .env (verbose Django error pages).
                     Only for troubleshooting on a test box — NEVER use
                     this on a real public production deploy. Without
                     this flag, DEBUG is always set to False, on every
                     distro.
  --reconfigure-env Re-ask for every .env value (domain, DB creds, admin
                     account, etc.), showing the current value as the
                     default (Enter keeps it). Without this flag, an
                     existing .env is reused AS-IS on repeat deploys —
                     nothing is re-asked or regenerated unless a value
                     is genuinely missing.
  --public-domain   Skip the "is this domain public?" prompt in step 5;
                     answer yes. Tailscale Funnel (step 11) is skipped.
  --private-domain  Skip the "is this domain public?" prompt in step 5;
                     answer no. Tailscale Funnel (step 11) is set up/
                     re-asserted so the mobile app can reach this server
                     from outside the LAN without a public domain.
  --help, -h        Show this help.

Rollback:
  If a deploy goes bad, sudo ./scripts/rollback.sh reverts the web
  container to the image that was running before the last build.

Examples:
  sudo ./deploy_production_webserver.sh
  sudo ./deploy_production_webserver.sh --skip-ssl --yes      # local/Kali test run
  sudo ./deploy_production_webserver.sh --staging-ssl         # dry-run real domain
HELP
}
