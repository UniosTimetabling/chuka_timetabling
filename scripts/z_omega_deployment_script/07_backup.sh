#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 07_backup.sh — pre-deploy snapshot of the database and media
# files, written to backups/<YYYY-MM-DD_HHMMSS>/ BEFORE anything
# disruptive happens (images rebuilt, containers recreated,
# migrations run against the new code).
#
# Skips cleanly on a first-ever deploy — there's nothing running
# yet to back up. A failed backup never aborts the deploy (a
# broken backup step shouldn't itself take down a working
# system) but it's loud about it in both the console and
# logs/deploy_history.log, since a silent skip here is exactly
# how you discover — the hard way, mid-incident — that the last
# few deploys never actually produced a restorable backup.
# ──────────────────────────────────────────────────────────────

run_pre_deploy_backup() {
  step "7/11  Backing up database and media (pre-deploy snapshot)"
  cd "$PROJECT_ROOT"
  mkdir -p "$PROJECT_ROOT/logs"

  if ! docker compose ps -q db 2>/dev/null | grep -q .; then
    info "No existing 'db' container yet — nothing to back up (first deploy)."
    return 0
  fi

  local db_container db_running
  db_container=$(docker compose ps -q db)
  db_running=$(docker inspect -f '{{.State.Running}}' "$db_container" 2>/dev/null || echo "false")
  if [[ "$db_running" != "true" ]]; then
    info "'db' container exists but isn't running — nothing to back up."
    return 0
  fi

  local ts backup_dir
  ts="$(date +%Y-%m-%d_%H%M%S)"
  backup_dir="$PROJECT_ROOT/backups/${ts}"
  mkdir -p "$backup_dir"
  info "Snapshot folder: backups/${ts}/"

  # ---- Database (mysqldump, gzip-piped straight to disk) ------------------
  local db_name db_user db_pass db_ok=false
  db_name=$(get_env_var DB_NAME "$PROJECT_ROOT/.env")
  db_user=$(get_env_var DB_USER "$PROJECT_ROOT/.env")
  db_pass=$(get_env_var DB_PASSWORD "$PROJECT_ROOT/.env")

  if [[ -z "$db_name" || -z "$db_user" ]]; then
    warn "Could not read DB_NAME/DB_USER from .env — skipping database backup."
  else
    info "Dumping database '${db_name}'..."
    if docker compose exec -T -e MYSQL_PWD="$db_pass" db \
         mysqldump --single-transaction --quick -u"$db_user" "$db_name" \
         2>"$backup_dir/mysqldump.err" | gzip > "$backup_dir/db.sql.gz"; then
      if [[ -s "$backup_dir/db.sql.gz" ]]; then
        db_ok=true
        ok "Database backup saved: db.sql.gz ($(du -h "$backup_dir/db.sql.gz" | cut -f1))"
      else
        warn "mysqldump produced an empty file — check $backup_dir/mysqldump.err"
      fi
    else
      warn "mysqldump failed — see $backup_dir/mysqldump.err. Continuing deploy WITHOUT a fresh DB backup."
    fi
  fi

  # ---- Media (tar the named volume via a disposable alpine container) -----
  local media_vol media_ok=false
  media_vol=$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/app/media" }}{{ .Name }}{{ end }}{{ end }}' chuka_web 2>/dev/null || true)
  if [[ -n "$media_vol" ]]; then
    info "Archiving media volume '${media_vol}'..."
    if docker run --rm \
         -v "${media_vol}:/from:ro" \
         -v "$backup_dir:/backup" \
         alpine:3 sh -c "cd /from && tar czf /backup/media.tar.gz ." \
         2>"$backup_dir/media_tar.err"; then
      media_ok=true
      ok "Media backup saved: media.tar.gz ($(du -h "$backup_dir/media.tar.gz" 2>/dev/null | cut -f1))"
    else
      warn "Media archive failed — see $backup_dir/media_tar.err. Continuing deploy WITHOUT a fresh media backup."
    fi
  else
    warn "Could not locate the media volume (no prior chuka_web container?) — skipping media backup."
  fi

  echo "$(date -Iseconds)  BACKUP   dir=${backup_dir}  db=${db_ok}  media=${media_ok}  by=$(whoami)  host=$(hostname)" >> "$PROJECT_ROOT/logs/deploy_history.log"

  if [[ "$db_ok" == "true" || "$media_ok" == "true" ]]; then
    ok "Pre-deploy backup complete: backups/${ts}/"
  else
    warn "Pre-deploy backup did NOT produce anything usable — proceeding anyway, but you have no fresh restore point for this deploy."
  fi

  # ---- Prune old snapshots: keep the most recent 14 ------------------------
  local keep=14 total
  total=$(find "$PROJECT_ROOT/backups" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
  if (( total > keep )); then
    info "Pruning old backups (keeping the most recent ${keep} of ${total})..."
    find "$PROJECT_ROOT/backups" -mindepth 1 -maxdepth 1 -type d | sort | head -n "-${keep}" | xargs -r rm -rf
  fi
}
