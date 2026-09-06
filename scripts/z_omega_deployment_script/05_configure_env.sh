#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 05_configure_env.sh — build/confirm .env before anything else
# touches Docker.
#
# Default behavior (repeat deploys): if .env already exists and a
# given value is already set, that value is reused AS-IS, with no
# prompt — this is what makes it safe to re-run
# deploy_production_webserver.sh on a live box without it silently
# asking you to re-type (or worse, re-generating) things like your
# domain, DB credentials, or SECRET_KEY. Only fields that are
# genuinely missing (fresh install, or a field added by a newer
# version of this script) get asked for.
#
# Pass --reconfigure-env if you actually want to change existing
# values — that restores the old behavior of prompting for every
# field, showing the current value as the default (Enter keeps it).
#
# Either way: a summary is shown and requires confirmation before
# anything gets written to Docker (or bails out so you can edit
# .env by hand instead).
# ──────────────────────────────────────────────────────────────

configure_env() {
  step "5/11  Configuring environment (.env)"

  local ENV_FILE="$PROJECT_ROOT/.env"
  local ENV_EXAMPLE="$PROJECT_ROOT/.env.example"
  local RECONFIGURE="${RECONFIGURE_ENV:-false}"

  if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "$ENV_EXAMPLE" ]] || fail ".env.example not found — cannot bootstrap .env."
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    info "Created .env from .env.example."
  elif [[ "$RECONFIGURE" == "true" ]]; then
    info ".env already exists — --reconfigure-env passed, so every value below will be re-asked (current value shown as the default; Enter keeps it)."
  else
    info ".env already exists — reusing its existing values as-is. Only asking about anything genuinely missing. Pass --reconfigure-env to change existing values instead."
  fi

  if [[ "${SKIP_SSL:-false}" == "true" ]]; then
    DEFAULT_DOMAIN="localhost"
  else
    DEFAULT_DOMAIN=""
  fi

  local domain email db_name db_user db_pass db_root_pass secret debug_mode

  # existing_or_ask KEY PROMPT DEFAULT -> echoes the value to use.
  # If .env already has a non-empty value for KEY and we're not in
  # --reconfigure-env mode, that value is used untouched, no prompt at
  # all. Otherwise falls through to the normal ask() (which itself
  # honors AUTO_YES / non-interactive runs).
  existing_or_ask() {
    local key="$1" prompt="$2" default="$3" current
    current=$(get_env_var "$key" "$ENV_FILE" || true)
    if [[ -n "$current" && "$RECONFIGURE" != "true" ]]; then
      echo "$current"
    else
      ask "$prompt" "${current:-$default}"
    fi
  }

  domain=$(existing_or_ask SSL_DOMAIN "Production domain (e.g. timetabling.chuka.ac.ke)" "$DEFAULT_DOMAIN")
  [[ -z "$domain" ]] && fail "A domain is required (use 'localhost' only for --skip-ssl test runs)."

  # ---- Public domain vs. Tailscale Funnel --------------------------------
  # There's no public API/domain reachable from outside this LAN yet in a
  # lot of these deploys — this app still needs to be reachable by the
  # mobile app from OUTSIDE the local network somehow. If the operator
  # says this domain/box is NOT already publicly reachable (real DNS +
  # their own port-forwarding, not just Let's Encrypt working locally),
  # 11_tailscale_setup.sh brings up a Tailscale Funnel instead so mobile
  # devices have a stable way in without one.
  #
  # Persisted to .env (like every other value here) so repeat/"update"
  # deploys don't re-ask and don't skip re-asserting Tailscale Funnel is
  # still up — see setup_tailscale's "idempotent, re-run every deploy"
  # note. Pass --public-domain / --private-domain to set this
  # non-interactively (also honored the very first time, no prompt at
  # all), or --reconfigure-env to be asked again despite an existing
  # answer.
  local public_domain_prev
  public_domain_prev=$(get_env_var "DEPLOY_PUBLIC_DOMAIN" "$ENV_FILE" || true)

  if [[ "${FORCE_PUBLIC_DOMAIN_SET:-false}" == "true" ]]; then
    PUBLIC_DOMAIN="$FORCE_PUBLIC_DOMAIN"
    info "Public domain: ${PUBLIC_DOMAIN} (set via --$([[ "$PUBLIC_DOMAIN" == "true" ]] && echo public || echo private)-domain)."
  elif [[ -n "$public_domain_prev" && "$RECONFIGURE" != "true" ]]; then
    PUBLIC_DOMAIN="$public_domain_prev"
    info "Public domain: ${PUBLIC_DOMAIN} (reused from previous deploy — pass --reconfigure-env to change)."
  elif [[ "$domain" == "localhost" ]]; then
    if confirm "No public domain configured (using 'localhost'). Set up Tailscale Funnel now so the mobile app can reach this server from outside this network?"; then
      PUBLIC_DOMAIN="false"
    else
      PUBLIC_DOMAIN="true"
      info "Skipping Tailscale — the mobile app will only reach this server from inside this LAN for now."
    fi
  else
    if confirm "Is '$domain' already publicly reachable from the internet right now (real DNS pointed here AND your own port-forwarding/security-group rules already in place)?"; then
      PUBLIC_DOMAIN="true"
    else
      PUBLIC_DOMAIN="false"
      info "Will set up Tailscale Funnel (step 11) so the mobile app has a stable way to reach this server without a public domain."
    fi
  fi
  set_env_var "DEPLOY_PUBLIC_DOMAIN" "$PUBLIC_DOMAIN" "$ENV_FILE"
  export PUBLIC_DOMAIN

  # SSL_EMAIL is only ever used as the contact address Let's Encrypt emails
  # for renewal-failure/expiry warnings — it has no effect on the cert
  # itself and doesn't need to match the domain.
  email=$(existing_or_ask SSL_EMAIL "Contact email for Let's Encrypt renewal/expiry notices (any address you check — Gmail, Chuka, etc.)" "admin@${domain}")

  if [[ "${SKIP_SSL:-false}" != "true" && "$domain" != "localhost" ]]; then
    [[ -z "$email" ]] && fail "An email is required for Let's Encrypt (or pass --skip-ssl)."
  fi

  db_name=$(existing_or_ask DB_NAME "Database name" "timetabling_db")
  db_user=$(existing_or_ask DB_USER "Database user" "timetabling_user")

  db_pass=$(get_env_var DB_PASSWORD "$ENV_FILE" || true)
  if [[ -z "$db_pass" || "$db_pass" == "changeme" ]]; then
    if confirm "No strong DB password set — auto-generate one?"; then
      db_pass=$(generate_secret_key | cut -c1-24)
    else
      db_pass=$(ask "Database password" "changeme")
    fi
  elif [[ "$RECONFIGURE" == "true" ]]; then
    db_pass=$(ask "Database password (leave as-is unless you're actually rotating it)" "$db_pass")
  fi

  db_root_pass=$(get_env_var DB_ROOT_PASSWORD "$ENV_FILE" || true)
  if [[ -z "$db_root_pass" || "$db_root_pass" == "rootchangeme" ]]; then
    db_root_pass=$(generate_secret_key | cut -c1-24)
  fi

  secret=$(get_env_var SECRET_KEY "$ENV_FILE" || true)
  if [[ -z "$secret" || "$secret" == django-insecure-* ]]; then
    info "Generating a fresh production SECRET_KEY..."
    secret=$(generate_secret_key)
  fi

  # ---- Django admin superuser --------------------------------------------
  # Created (idempotently, only if no superuser exists yet) by
  # docker-entrypoint.sh after migrations run. Unlike DB_PASSWORD, this
  # password is actually typed by a human to log in — so we show it in
  # full in the summary below rather than masking it, and offer to let
  # them set their own instead of forcing an auto-generated one on them
  # (which people find hard to remember/type). A custom password still
  # has to clear the same strength bar as the DB/secret-key passwords.
  local admin_user admin_email admin_pass admin_pass_is_new=false
  admin_user=$(existing_or_ask DJANGO_SUPERUSER_USERNAME "Admin (superuser) username" "admin")
  admin_email=$(existing_or_ask DJANGO_SUPERUSER_EMAIL "Admin email" "$email")

  admin_pass=$(get_env_var DJANGO_SUPERUSER_PASSWORD "$ENV_FILE" || true)
  if [[ -z "$admin_pass" || "$admin_pass" == "changeme" ]]; then
    if [[ "${AUTO_YES:-false}" == "true" || ! -t 0 ]]; then
      # Non-interactive run (--yes / no TTY) — nobody's here to type one
      # in, so fall back to a strong auto-generated password.
      info "Non-interactive run — auto-generating a strong admin password."
      admin_pass=$(generate_secret_key | cut -c1-16)
      admin_pass_is_new=true
    elif confirm "Set your own admin password now (instead of an auto-generated one)?"; then
      local candidate confirm_candidate issues attempts=0
      while true; do
        attempts=$((attempts + 1))
        candidate=$(ask_secret "Admin password")
        if [[ -z "$candidate" ]]; then
          warn "No password entered — try again."
          continue
        fi
        issues=$(password_strength_issues "$candidate")
        if [[ -n "$issues" ]]; then
          warn "That password isn't strong enough. It needs:"
          while IFS= read -r line; do [[ -n "$line" ]] && warn "  - $line"; done <<< "$issues"
          continue
        fi
        confirm_candidate=$(ask_secret "Confirm admin password")
        if [[ "$candidate" != "$confirm_candidate" ]]; then
          warn "Passwords didn't match — try again."
          continue
        fi
        admin_pass="$candidate"
        admin_pass_is_new=true
        break
      done
    else
      info "Auto-generating a strong admin password..."
      admin_pass=$(generate_secret_key | cut -c1-16)
      admin_pass_is_new=true
    fi
  fi

  # DEBUG always defaults to False, on every distro. Pass --debug
  # explicitly if you actually want verbose Django error pages for
  # troubleshooting on a test box.
  if [[ "${FORCE_DEBUG:-false}" == "true" ]]; then
    debug_mode="True"
    warn "DEBUG=True forced via --debug — verbose error pages will be shown."
    warn "Do NOT use --debug on a real public production deploy."
  else
    debug_mode="False"
  fi

  # ---- write everything to .env ------------------------------------------
  set_env_var "SECRET_KEY" "$secret" "$ENV_FILE"
  set_env_var "DEBUG" "$debug_mode" "$ENV_FILE"
  # merge_env_csv_list (lib_common.sh), not set_env_var: these three keys
  # can each be contributed to by more than one step (this one, the
  # Tailscale Funnel setup in step 11) or by hand (a debug tunnel
  # hostname), so a plain overwrite here would silently erase whatever
  # those already added — on this box or any other. CORS_ALLOWED_ORIGINS
  # is included here too so the production domain itself is reachable by
  # browser-based JS calling the API, which nothing was previously adding
  # it to at all.
  merge_env_csv_list "ALLOWED_HOSTS" "${domain},www.${domain},127.0.0.1,localhost" "$ENV_FILE" >/dev/null
  merge_env_csv_list "CSRF_TRUSTED_ORIGINS" "https://${domain},https://www.${domain}" "$ENV_FILE" >/dev/null
  merge_env_csv_list "CORS_ALLOWED_ORIGINS" "https://${domain},https://www.${domain},http://localhost:8081,http://127.0.0.1:8081" "$ENV_FILE" >/dev/null
  set_env_var "DB_NAME" "$db_name" "$ENV_FILE"
  set_env_var "DB_USER" "$db_user" "$ENV_FILE"
  set_env_var "DB_PASSWORD" "$db_pass" "$ENV_FILE"
  set_env_var "DB_ROOT_PASSWORD" "$db_root_pass" "$ENV_FILE"
  set_env_var "SSL_DOMAIN" "$domain" "$ENV_FILE"
  set_env_var "SSL_DOMAIN_WWW" "www.${domain}" "$ENV_FILE"
  set_env_var "SSL_EMAIL" "$email" "$ENV_FILE"
  set_env_var "DJANGO_SUPERUSER_USERNAME" "$admin_user" "$ENV_FILE"
  set_env_var "DJANGO_SUPERUSER_EMAIL" "$admin_email" "$ENV_FILE"
  set_env_var "DJANGO_SUPERUSER_PASSWORD" "$admin_pass" "$ENV_FILE"

  # ---- summary + confirmation gate ---------------------------------------
  echo
  info "${BOLD}Review configuration before we build anything:${RESET}"
  printf "      %-22s %s\n" "Domain:"        "$domain"
  printf "      %-22s %s\n" "SSL email:"     "$email"
  printf "      %-22s %s\n" "DEBUG:"         "$debug_mode$([[ "$debug_mode" == "True" ]] && echo ' (forced via --debug — DO NOT use on real production)')"
  printf "      %-22s %s\n" "DB name:"       "$db_name"
  printf "      %-22s %s\n" "DB user:"       "$db_user"
  printf "      %-22s %s\n" "DB password:"   "$(mask "$db_pass")"
  printf "      %-22s %s\n" "DB root pass:"  "$(mask "$db_root_pass")"
  printf "      %-22s %s\n" "SECRET_KEY:"    "$(mask "$secret")"
  printf "      %-22s %s\n" "SSL mode:"      "$([[ "${SKIP_SSL:-false}" == "true" ]] && echo "self-signed (test)" || echo "Let's Encrypt")"
  printf "      %-22s %s\n" "Public domain:"  "$([[ "$PUBLIC_DOMAIN" == "true" ]] && echo "yes" || echo "no — Tailscale Funnel will be (re)configured in step 11")"
  printf "      %-22s %s\n" "Admin username:" "$admin_user"
  printf "      %-22s %s\n" "Admin email:"    "$admin_email"
  if $admin_pass_is_new; then
    printf "      %-22s %s\n" "Admin password:" "$admin_pass"
    warn "That's the admin password that will be set — SAVE IT NOW, it won't be shown again."
  else
    printf "      %-22s %s\n" "Admin password:" "(unchanged from previous deploy)"
  fi
  echo

  confirm "Proceed with these settings?" || {
    info "Aborted. Edit .env by hand, or re-run and answer the prompts differently."
    exit 1
  }

  export DOMAIN="$domain" SSL_EMAIL_ADDR="$email"
}
