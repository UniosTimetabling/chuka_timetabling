#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 05_ssl_setup.sh — guarantees docker/nginx/certs/{fullchain.pem,
# privkey.pem} exist before nginx ever starts (that config always
# expects them, so this is a hard prerequisite, not optional).
#
#   --skip-ssl / test OS / domain=localhost  -> self-signed cert
#   otherwise                                -> real Let's Encrypt
#                                                cert via scripts/init-letsencrypt.sh
#
# Idempotent: re-running does nothing if a matching cert already
# exists, unless --force-ssl is passed.
# ──────────────────────────────────────────────────────────────

setup_ssl() {
  step "6/11  SSL certificates"

  local CERTS_DIR="$PROJECT_ROOT/docker/nginx/certs"
  mkdir -p "$CERTS_DIR"
  local cert="$CERTS_DIR/fullchain.pem" key="$CERTS_DIR/privkey.pem"

  local need_selfsigned=false
  if [[ "${SKIP_SSL:-false}" == "true" || "$DOMAIN" == "localhost" ]]; then
    need_selfsigned=true
  fi

  if [[ -f "$cert" && -f "$key" && "${FORCE_SSL:-false}" != "true" ]]; then
    local issuer
    issuer=$(openssl x509 -noout -issuer -in "$cert" 2>/dev/null || echo "unknown")
    ok "Certificate already present (issuer: $issuer) — skipping (use --force-ssl to redo)."
    return
  fi

  if $need_selfsigned; then
    if command -v mkcert >/dev/null 2>&1; then
      info "mkcert found — issuing a locally-trusted certificate for '$DOMAIN' (no browser warning)."
      # mkcert -install adds its local CA to your OS/browser trust stores.
      # Safe to call every run: it's a no-op if already installed.
      mkcert -install >/dev/null 2>&1 || warn "mkcert -install failed — cert will still work, but the CA may not be trusted yet. Try running 'mkcert -install' manually."
      mkcert -cert-file "$cert" -key-file "$key" "$DOMAIN" "www.${DOMAIN}" localhost 127.0.0.1 ::1 >/dev/null 2>&1 \
        && ok "Locally-trusted cert written to $CERTS_DIR (issued by your machine's mkcert CA — Chrome will trust it)." \
        || { warn "mkcert failed — falling back to a plain self-signed cert."; need_selfsigned=true; }
    fi
    if [[ ! -f "$cert" || ! -f "$key" ]]; then
      info "Generating a self-signed certificate for '$DOMAIN' (TEST/LOCAL USE ONLY)."
      warn "Browsers will show a security warning until this is replaced with a real cert."
      info "Tip: install mkcert (https://github.com/FiloSottile/mkcert) and re-run with --force-ssl for a locally-trusted cert with no browser warning."
      openssl req -x509 -nodes -days 825 \
        -newkey rsa:2048 \
        -keyout "$key" \
        -out "$cert" \
        -subj "/C=KE/ST=Kenya/L=Chuka/O=ChukaUniversity/CN=${DOMAIN}" \
        -addext "subjectAltName=DNS:${DOMAIN},DNS:www.${DOMAIN},DNS:localhost" \
        2>/dev/null
      chmod 644 "$cert"; chmod 600 "$key"
      ok "Self-signed cert written to $CERTS_DIR."
    fi
  else
    info "Requesting a real certificate for '$DOMAIN' via Let's Encrypt..."
    local ssl_flags=(-d "$DOMAIN" -d "www.${DOMAIN}" -e "$SSL_EMAIL_ADDR")
    [[ "${STAGING_SSL:-false}" == "true" ]] && ssl_flags+=(-n)

    # This is the standalone script from the previous step — it needs
    # port 80 free, which is why SSL setup happens BEFORE docker compose up.
    bash "$PROJECT_ROOT/scripts/init-letsencrypt.sh" "${ssl_flags[@]}" \
      || fail "Let's Encrypt issuance failed. Re-run with --staging-ssl to test safely, or --skip-ssl for a self-signed cert."
    ok "Let's Encrypt certificate installed."
  fi
}
