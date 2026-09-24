#!/usr/bin/env bash
# =============================================================================
# run.sh — one-command local launcher for the Chuka Timetabling desktop app.
#
#   ./run.sh              install anything missing, build, start the app
#   ./run.sh --fresh      same, but first wipe the local test database
#   ./run.sh --reinstall  same, but first delete node_modules and reinstall
#
# It does NOT start the Django backend — start that yourself first, listening on
# 127.0.0.1:8001 (e.g.  python manage.py runserver 127.0.0.1:8001).
#
# What it does, in order:
#   1. checks Node/npm are installed and recent enough
#   2. installs npm dependencies (and rebuilds better-sqlite3 for Electron) —
#      only when package.json / package-lock.json changed since the last run
#   3. checks the backend answers at $CHUKA_BACKEND_URL (warns, never blocks)
#   4. builds the renderer + main process, with the login screen pre-filled
#      with the backend address
#   5. launches Electron against a SEPARATE local-test database, so your real
#      offline data (and any production login) is never touched or wiped
#
# Overrides (environment variables):
#   CHUKA_BACKEND_URL   backend address           (default http://127.0.0.1:8001)
#   CHUKA_DB_PATH       local-test SQLite file    (default ./.local-test/chuka_local_test.sqlite3)
#   CHUKA_NO_SANDBOX=1  force Electron's --no-sandbox (auto-enabled on Linux when needed)
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

BACKEND_URL="${CHUKA_BACKEND_URL:-http://127.0.0.1:8001}"
BACKEND_URL="${BACKEND_URL%/}"
export CHUKA_DB_PATH="${CHUKA_DB_PATH:-$HERE/.local-test/chuka_local_test.sqlite3}"
ELECTRON_BIN="$HERE/node_modules/.bin/electron"
STAMP="$HERE/node_modules/.run-sh-deps-stamp"
MIN_NODE_MAJOR=18

# ---- output helpers ---------------------------------------------------------
if [[ -t 1 ]]; then B=$'\033[1m'; G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; N=$'\033[0m'; else B=""; G=""; Y=""; R=""; N=""; fi
step() { echo; echo "${B}[$1]${N} $2"; }
ok()   { echo "  ${G}✔${N} $*"; }
warn() { echo "  ${Y}!${N} $*" >&2; }
die()  { echo "  ${R}✘ $*${N}" >&2; exit 1; }

usage() { sed -n '3,/^# =====/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

FRESH=0; REINSTALL=0
for arg in "$@"; do
  case "$arg" in
    --fresh)     FRESH=1 ;;
    --reinstall) REINSTALL=1 ;;
    -h|--help)   usage; exit 0 ;;
    *)           echo "Unknown option: $arg" >&2; usage; exit 2 ;;
  esac
done

hash_files() {
  if command -v sha256sum >/dev/null 2>&1; then cat "$@" | sha256sum | cut -d' ' -f1
  else cat "$@" | shasum -a 256 | cut -d' ' -f1; fi
}

# Prints the HTTP status the server answers with, or 000 if nothing is listening.
probe() {
  node -e '
    fetch(process.argv[1], { redirect: "manual", signal: AbortSignal.timeout(4000) })
      .then(r => console.log(r.status))
      .catch(() => console.log("000"));
  ' "$1"
}

# Can better-sqlite3 load inside Electron's own Node? (Catches ABI mismatches before the app opens.)
native_ok() {
  ELECTRON_RUN_AS_NODE=1 "$ELECTRON_BIN" -e "new (require('better-sqlite3'))(':memory:').close()" >/dev/null 2>&1
}

# Linux only: is Electron's sandbox unusable here? (Ubuntu 23.10+/24.04 block unprivileged user
# namespaces via AppArmor, and npm-installed Electron has no SUID chrome-sandbox helper; running as
# root also needs it off.) Local testing only — the app never loads remote content.
sandbox_unusable() {
  [[ "${CHUKA_NO_SANDBOX:-0}" == "1" ]] && return 0
  [[ "$(id -u)" -eq 0 ]] && return 0
  local sb="$HERE/node_modules/electron/dist/chrome-sandbox" f
  if [[ -f "$sb" && -u "$sb" && "$(stat -c %u "$sb" 2>/dev/null)" == "0" ]]; then return 1; fi
  f=/proc/sys/kernel/apparmor_restrict_unprivileged_userns
  if [[ -r "$f" && "$(cat "$f")" == "1" ]]; then return 0; fi
  f=/proc/sys/kernel/unprivileged_userns_clone
  if [[ -r "$f" && "$(cat "$f")" == "0" ]]; then return 0; fi
  return 1
}

# ---- 1. Node / npm ----------------------------------------------------------
step "1/5" "Checking Node.js and npm"
command -v node >/dev/null 2>&1 || die "Node.js is not installed. Install Node ${MIN_NODE_MAJOR}+ (https://nodejs.org, or: nvm install 20) and re-run."
command -v npm  >/dev/null 2>&1 || die "npm is not installed (it normally ships with Node.js)."
NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
(( NODE_MAJOR >= MIN_NODE_MAJOR )) || die "Node $(node -v) is too old; need ${MIN_NODE_MAJOR}+. (nvm install 20)"
ok "node $(node -v), npm $(npm -v)"

# ---- 2. Dependencies --------------------------------------------------------
step "2/5" "Checking dependencies"
[[ $REINSTALL -eq 1 ]] && { warn "--reinstall: removing node_modules"; rm -rf node_modules; }

if [[ -d node_modules && -f "$STAMP" && "$(cat "$STAMP")" == "$(hash_files package.json package-lock.json)" && -x "$ELECTRON_BIN" ]]; then
  ok "npm packages up to date"
else
  echo "  Installing npm packages (first run downloads Electron ~100 MB and rebuilds better-sqlite3)…"
  if ! npm install --no-audit --no-fund; then
    die "npm install failed. If the error mentions node-gyp / gcc / make, install build tools
    (Ubuntu/Debian: sudo apt install build-essential python3 · macOS: xcode-select --install) and re-run.
    If it mentions downloading Electron, check your internet connection / proxy."
  fi
  # Hash AFTER installing: npm may touch package-lock.json (e.g. adds hasInstallScript), which would
  # otherwise make every later run think the dependencies changed.
  hash_files package.json package-lock.json > "$STAMP"
  ok "npm packages installed"
fi

[[ -x "$ELECTRON_BIN" ]] || die "Electron binary missing after install. Try: ./run.sh --reinstall"
if native_ok; then
  ok "better-sqlite3 loads under Electron"
else
  warn "better-sqlite3 doesn't match Electron's Node version — rebuilding…"
  npx --no-install electron-builder install-app-deps || true
  native_ok || die "better-sqlite3 still won't load under Electron. Try: ./run.sh --reinstall (needs build tools: build-essential/python3 on Linux, Xcode CLT on macOS)."
  ok "better-sqlite3 rebuilt"
fi

# ---- 3. Backend -------------------------------------------------------------
step "3/5" "Checking backend at ${BACKEND_URL}"
STATUS="000"
for attempt in 1 2 3; do
  STATUS="$(probe "$BACKEND_URL/api/desktop/auth/me/")"
  [[ "$STATUS" != "000" ]] && break
  (( attempt < 3 )) && sleep 2
done
case "$STATUS" in
  000) warn "Nothing is answering at ${BACKEND_URL}."
       warn "Start your Django backend first, e.g.:  python manage.py runserver 127.0.0.1:8001"
       warn "Launching anyway — the app opens, but sign-in will fail until the backend is up." ;;
  404) warn "Backend is up, but /api/desktop/ returned 404 — is desktop_sync in INSTALLED_APPS and its urls included?" ;;
  400) warn "Backend is up but rejected the request (400) — check ALLOWED_HOSTS includes 127.0.0.1." ;;
  5??) warn "Backend is up but returned HTTP ${STATUS} — check the Django console for the traceback." ;;
  *)   ok "Backend is up and the desktop API is mounted (HTTP ${STATUS})" ;;
esac

# ---- 4. Build ---------------------------------------------------------------
step "4/5" "Building (login screen will default to ${BACKEND_URL})"
VITE_DEFAULT_SERVER_URL="$BACKEND_URL" npm run build || die "Build failed — see the errors above."
ok "build complete"

# ---- 5. Launch --------------------------------------------------------------
step "5/5" "Starting the app"
mkdir -p "$(dirname "$CHUKA_DB_PATH")"
if [[ $FRESH -eq 1 ]]; then
  rm -f "$CHUKA_DB_PATH" "$CHUKA_DB_PATH-wal" "$CHUKA_DB_PATH-shm"
  ok "--fresh: local test database wiped"
fi
ok "local test database: $CHUKA_DB_PATH (separate from your real offline data)"

ELECTRON_ARGS=(.)
if [[ "$(uname -s)" == "Linux" ]]; then
  if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]; then
    die "No display found (DISPLAY / WAYLAND_DISPLAY are empty), so a desktop window can't open.
    On WSL2 you need WSLg (Windows 11 / recent Windows 10 + wsl --update); over SSH use a desktop session."
  fi
  if sandbox_unusable; then
    ELECTRON_ARGS+=(--no-sandbox)
    warn "Using Electron --no-sandbox (this system blocks the sandbox for npm-installed Electron; fine for local testing)."
  fi
fi

echo
echo "  Sign in with a Timetable Office account that exists on ${BACKEND_URL}."
echo "  Close the app window (or press Ctrl+C here) to stop."
echo

# VS Code's integrated terminal sets ELECTRON_RUN_AS_NODE=1, which would make Electron behave as plain Node.
unset ELECTRON_RUN_AS_NODE
exec "$ELECTRON_BIN" "${ELECTRON_ARGS[@]}"
