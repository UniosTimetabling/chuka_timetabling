#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 01_detect_os.sh — identify the OS so later steps use the right
# package manager and install commands.
#
# Every Linux distro is treated as an equally valid PRODUCTION
# target — Ubuntu, Debian, Kali, Arch, Alpine, Fedora, whatever a
# given IoT/edge box happens to ship. This script does not grade
# distros as "real production" vs. "just a test box"; that's a
# decision for the operator (via --skip-ssl / --debug / how they
# invoke it), not something to infer from /etc/os-release.
#
# What DOES vary by distro is package-manager support for the
# automated dependency install in step 2 (02_check_dependencies.sh),
# which currently only knows how to drive apt. That's a packaging
# gap, not a production/test distinction, so it's surfaced as
# PKG_MANAGER_SUPPORTED rather than blocking or downgrading anything.
# ──────────────────────────────────────────────────────────────

OS_ID=""
OS_PRETTY=""
PKG_MANAGER_SUPPORTED=false

detect_os() {
  step "1/11  Detecting operating system"

  if [[ ! -f /etc/os-release ]]; then
    fail "Cannot find /etc/os-release — can't identify this OS at all. This script needs a standard Linux distro (any distro; it reads /etc/os-release to know which package manager to drive)."
  fi

  # shellcheck disable=SC1091
  source /etc/os-release
  OS_ID="${ID:-unknown}"
  OS_PRETTY="${PRETTY_NAME:-$OS_ID}"

  case "$OS_ID" in
    ubuntu|debian|kali)
      PKG_MANAGER_SUPPORTED=true
      ok "Detected $OS_PRETTY — production target, apt-based dependency install supported."
      ;;
    *)
      if [[ "${ID_LIKE:-}" == *debian* ]]; then
        PKG_MANAGER_SUPPORTED=true
        ok "Detected $OS_PRETTY (debian-like) — production target, apt-based dependency install supported."
      else
        PKG_MANAGER_SUPPORTED=false
        ok "Detected $OS_PRETTY — production target."
        warn "This distro's package manager isn't apt, so step 2's automated"
        warn "dependency install (Docker, certbot, curl, etc. via apt-get) won't"
        warn "run here. Install those yourself first (e.g. pacman on Arch, apk"
        warn "on Alpine, dnf on Fedora), then re-run with --no-deps. Everything"
        warn "after that — Docker, SSL, firewall, the app itself — runs the same"
        warn "on this box as on any other production host."
      fi
      ;;
  esac

  if [[ $EUID -ne 0 ]]; then
    fail "This script must be run as root (sudo ./deploy_production_webserver.sh ...) — it installs packages, binds ports 80/443, and manages Docker."
  fi

  export OS_ID OS_PRETTY PKG_MANAGER_SUPPORTED
}
