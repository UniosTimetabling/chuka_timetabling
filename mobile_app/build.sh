#!/usr/bin/env bash
#
# build.sh — Package Chuka Timetable for real-world distribution.
#
# Produces:
#   apk       An .apk you can install directly on any Android phone
#             (side-load / share via link — no Play Store needed).
#   aab       A Play Store–ready .aab (Android App Bundle).
#   submit    Upload the latest aab build to the Play Console
#             (internal testing track, draft release).
#   ios       A Play-Store-equivalent .ipa for TestFlight/App Store
#             (only relevant if you ever target iOS).
#   all       apk + aab in one run.
#
# Usage:
#   ./build.sh apk
#   ./build.sh aab
#   ./build.sh aab --local        # build on this machine instead of EAS cloud
#   ./build.sh submit
#   ./build.sh all
#
# First-time setup this script expects:
#   1. A free Expo account -> https://expo.dev/signup
#   2. `eas login` run once (the script will prompt you if needed)
#   3. src/config/config.js BASE_URL pointed at your real production domain
#      (currently http://127.0.0.1:8001 — the script will refuse to build
#      "aab"/"submit" until you fix this, to stop you shipping a build that
#      only works on localhost).
#
set -euo pipefail

# ---------------------------------------------------------------------------
# Pretty output helpers
# ---------------------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}==>${NC} $1"; }
ok()    { echo -e "${GREEN}✔${NC} $1"; }
warn()  { echo -e "${YELLOW}⚠${NC} $1"; }
fail()  { echo -e "${RED}✘${NC} $1"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CONFIG_FILE="src/config/config.js"
MODE="${1:-}"
LOCAL_FLAG="${2:-}"

if [[ -z "$MODE" ]]; then
  echo "Usage: ./build.sh [apk|aab|submit|ios|all] [--local]"
  exit 1
fi

# ---------------------------------------------------------------------------
# 0. Refuse to run as root/sudo
# ---------------------------------------------------------------------------
# Nothing here needs root, and running with sudo installs Node/nvm/npm/eas
# into root's home instead of yours — every later run (without sudo) then
# can't see any of it and looks broken. Run this as your normal user; the
# script installs things per-user and asks for a password only if a system
# package manager step genuinely needs one.
if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
  fail "Don't run this with sudo/as root — it installs Node/eas-cli for your normal user account.\n   Re-run as: ./build.sh $MODE ${LOCAL_FLAG}\n   (If a step genuinely needs elevated privileges, this script will ask for your password itself.)"
fi

# ---------------------------------------------------------------------------
# 1. Toolchain checks — auto-install anything missing, don't just complain
# ---------------------------------------------------------------------------
info "Checking toolchain..."

REQUIRED_NODE_MAJOR=18
NVM_DIR_DEFAULT="$HOME/.nvm"

node_ok() {
  command -v node >/dev/null 2>&1 && \
    [[ "$(node -e 'console.log(process.versions.node.split(".")[0])')" -ge "$REQUIRED_NODE_MAJOR" ]]
}

install_node_via_nvm() {
  info "Node.js missing or too old — installing via nvm (no sudo needed)..."
  export NVM_DIR="$NVM_DIR_DEFAULT"
  if [[ ! -s "$NVM_DIR/nvm.sh" ]]; then
    command -v curl >/dev/null 2>&1 || fail "curl is required to install nvm. Install curl and re-run."
    curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
  fi
  # nvm's own script isn't written for `set -u`/`set -e` strict mode and
  # throws spurious "unbound variable" errors under it — relax strict mode
  # just for the duration of sourcing/calling nvm, then restore it.
  set +euo pipefail
  # shellcheck source=/dev/null
  \. "$NVM_DIR/nvm.sh"
  nvm install --lts
  nvm use --lts
  set -euo pipefail
}

install_node_via_system_pm() {
  # Best-effort system-level install for environments where nvm can't run.
  # These need root for the package manager itself — sudo here (not for the
  # whole script) is fine and will prompt you for your password once.
  if command -v apt-get >/dev/null 2>&1; then
    info "Installing Node.js via apt (you may be asked for your password)..."
    sudo apt-get update -y && sudo apt-get install -y nodejs npm
  elif command -v dnf >/dev/null 2>&1; then
    info "Installing Node.js via dnf (you may be asked for your password)..."
    sudo dnf install -y nodejs npm
  elif command -v brew >/dev/null 2>&1; then
    info "Installing Node.js via Homebrew..."
    brew install node
  fi
}

if ! node_ok; then
  install_node_via_nvm || true
  if ! node_ok; then
    install_node_via_system_pm || true
  fi
  if ! node_ok; then
    fail "Could not auto-install Node ${REQUIRED_NODE_MAJOR}+. Install it manually (https://nodejs.org) and re-run this script."
  fi
fi
ok "Node $(node --version)"

# npm ships with Node, but double-check in case of a partial/system install.
if ! command -v npm >/dev/null 2>&1; then
  fail "npm not found even though Node is installed — your Node install looks broken. Reinstall Node and re-run."
fi

# Prefer pnpm since the project ships a pnpm-lock.yaml, fall back to npm.
if command -v pnpm >/dev/null 2>&1; then
  PM="pnpm"
elif [[ -f "pnpm-lock.yaml" ]]; then
  warn "pnpm-lock.yaml found but pnpm isn't installed — installing pnpm globally."
  npm install -g pnpm >/dev/null 2>&1
  PM="pnpm"
else
  PM="npm"
fi
ok "Using package manager: $PM"

if ! command -v eas >/dev/null 2>&1; then
  info "Installing eas-cli globally (one-time)..."
  npm install -g eas-cli
fi
ok "eas-cli $(eas --version)"

command -v git >/dev/null 2>&1 || fail "git is required (EAS Build uses it to package the app). Install git and re-run."
ok "git $(git --version | awk '{print $3}')"

# ---------------------------------------------------------------------------
# 2. Install dependencies
# ---------------------------------------------------------------------------
info "Installing dependencies..."
if [[ "$PM" == "pnpm" ]]; then
  pnpm install --frozen-lockfile || pnpm install
else
  npm install
fi
ok "Dependencies installed."

# Let Expo align native module versions with the installed SDK.
info "Checking Expo SDK / dependency compatibility..."
npx expo install --fix || warn "expo install --fix reported issues above — review before shipping."

# ---------------------------------------------------------------------------
# 2b. Disk space + upload hygiene
# ---------------------------------------------------------------------------
# EAS Build zips this whole folder and uploads the tarball before building
# anything remotely. Without an .easignore telling it what to skip, it will
# happily include node_modules (hundreds of MB) — which can exhaust local
# disk space while compressing, and shows up as a confusing "ENOSPC: no
# space left on device" error that has nothing to do with your code.
if [[ ! -f ".easignore" ]]; then
  info "No .easignore found — creating one so node_modules/.git aren't uploaded to EAS..."
  cat > .easignore << 'EOF'
node_modules
.expo
.git
android
ios
dist
web-build
*.log
*.tsbuildinfo
.DS_Store
google-play-service-account.json
EOF
  ok ".easignore created."
fi

# EAS Build determines what to upload via git: it walks UP from this folder
# looking for the nearest .git, then archives that entire repository (not
# just this subfolder) — .easignore only filters within whatever repo it
# finds. If this project lives inside a bigger repo (e.g. alongside a
# Django backend), EAS will happily try to upload that whole repo,
# databases and all. Giving this folder its own self-contained git repo
# makes EAS stop right here instead of walking further up.
GIT_TOPLEVEL="$(git -C . rev-parse --show-toplevel 2>/dev/null || true)"

# A commit needs an author/committer identity, and this machine may have no
# global git config at all — set these via env vars so it works regardless,
# without touching any global git config.
export GIT_AUTHOR_NAME="build.sh" GIT_AUTHOR_EMAIL="build@localhost"
export GIT_COMMITTER_NAME="build.sh" GIT_COMMITTER_EMAIL="build@localhost"

if [[ -z "$GIT_TOPLEVEL" ]]; then
  info "Initializing a dedicated git repo for this app (needed for EAS Build)..."
  git init -q
  git add -A
  git commit -q -m "Initial commit for EAS Build"
  ok "Local repo created — this only affects this folder, not any repo it may sit inside."
elif [[ "$GIT_TOPLEVEL" != "$SCRIPT_DIR" ]]; then
  warn "This folder is nested inside a larger git repo at: $GIT_TOPLEVEL"
  warn "EAS Build would try to upload that ENTIRE repo (this is what caused the ENOSPC error last time)."
  info "Scoping a dedicated git repo to just this app folder instead..."
  git init -q
  git add -A
  git commit -q -m "Initial commit for EAS Build"
  ok "This folder is now its own repo — EAS will only see files inside it, nothing from $GIT_TOPLEVEL."
else
  # Already its own repo — make sure new/changed files are committed so
  # EAS's upload (which follows git, not just what's on disk) sees them.
  if [[ -n "$(git status --porcelain)" ]]; then
    info "Committing local changes so EAS Build picks them up..."
    git add -A
    git commit -q -m "Update for build $(date -u +%Y-%m-%dT%H:%M:%SZ)" || true
  fi
fi


# Warn early if free space genuinely looks tight, and try to reclaim some
# automatically via package-manager caches before the upload step fails.
AVAILABLE_KB=$(df -Pk . | awk 'NR==2 {print $4}')
AVAILABLE_MB=$((AVAILABLE_KB / 1024))
if [[ "$AVAILABLE_MB" -lt 1024 ]]; then
  warn "Only ${AVAILABLE_MB}MB free on this disk — clearing package manager caches to free space..."
  npm cache clean --force >/dev/null 2>&1 || true
  command -v pnpm >/dev/null 2>&1 && pnpm store prune >/dev/null 2>&1 || true
  AVAILABLE_KB=$(df -Pk . | awk 'NR==2 {print $4}')
  AVAILABLE_MB=$((AVAILABLE_KB / 1024))
  if [[ "$AVAILABLE_MB" -lt 512 ]]; then
    fail "Still only ${AVAILABLE_MB}MB free after cleanup. Free up disk space (empty Trash, clear Downloads, 'docker system prune' if you use Docker, etc.) and re-run."
  fi
  ok "Freed up space — ${AVAILABLE_MB}MB now available."
fi

# ---------------------------------------------------------------------------
# 3. Expo/EAS account + project link
# ---------------------------------------------------------------------------
info "Checking Expo login..."
if ! eas whoami >/dev/null 2>&1; then
  warn "You're not logged in to Expo/EAS."
  eas login
fi
ok "Logged in as $(eas whoami)"

if ! grep -q '"projectId"' app.json 2>/dev/null && ! grep -q '"projectId"' app.config.js 2>/dev/null; then
  info "Linking this app to an EAS project (first time only)..."
  eas init
fi


# ---------------------------------------------------------------------------
# 4. Guard against shipping a localhost/dev backend URL
# ---------------------------------------------------------------------------
check_production_url() {
  if grep -qE "BASE_URL = '(http://127\.0\.0\.1|http://localhost|http://10\.)" "$CONFIG_FILE"; then
    fail "$CONFIG_FILE still points at a local/dev address.\n   Update BASE_URL to your real production domain (https://...) before building an aab/submit release.\n   For a quick side-loaded 'apk' test build on your own network, you can pass --allow-dev-url."
  fi
}

# ---------------------------------------------------------------------------
# 5. Build helpers
# ---------------------------------------------------------------------------
build_android() {
  local profile="$1"
  local flags=(--platform android --profile "$profile" --non-interactive)
  if [[ "$LOCAL_FLAG" == "--local" ]]; then
    flags+=(--local)
    warn "Local build selected: requires Android SDK + JDK installed on this machine."
  fi
  info "Starting EAS build (profile: $profile)..."
  eas build "${flags[@]}"
  ok "Build submitted/completed. Download link printed above, and also visible at https://expo.dev"
}

build_ios() {
  local flags=(--platform ios --profile production --non-interactive)
  if [[ "$LOCAL_FLAG" == "--local" ]]; then
    fail "iOS local builds require macOS + Xcode; run this on a Mac without --local, or use EAS cloud (default)."
  fi
  info "Starting EAS build for iOS..."
  eas build "${flags[@]}"
  ok "iOS build submitted. You'll need an Apple Developer account for App Store / TestFlight."
}

submit_android() {
  check_production_url
  if [[ ! -f "google-play-service-account.json" ]]; then
    fail "google-play-service-account.json not found in project root.\n   Generate one in Play Console -> Setup -> API access -> Create service account,\n   download the JSON key, and place it here (keep it out of git!)."
  fi
  info "Submitting latest Android build to Play Console (internal track, draft)..."
  eas submit --platform android --latest --profile production
  ok "Submitted. Log in to Play Console to promote the draft release."
}

# ---------------------------------------------------------------------------
# 6. Dispatch
# ---------------------------------------------------------------------------
case "$MODE" in
  apk)
    info "Building installable APK (preview profile) — this works even with a dev/localhost BASE_URL, since it's for direct install/testing, not the Play Store."
    build_android preview
    ;;
  aab)
    check_production_url
    build_android production
    ;;
  submit)
    submit_android
    ;;
  ios)
    build_ios
    ;;
  all)
    build_android preview
    check_production_url
    build_android production
    ;;
  *)
    fail "Unknown mode '$MODE'. Use: apk | aab | submit | ios | all"
    ;;
esac

echo
ok "Done. Next steps:"
echo "   • apk build  -> share the download link, or scan the QR code EAS prints, to install directly on a phone."
echo "   • aab build  -> upload manually once via Play Console, then use './build.sh submit' for future updates."
echo "   • Bump 'version' in app.json before each new release users should see as an update; versionCode/buildNumber auto-increment via eas.json."
