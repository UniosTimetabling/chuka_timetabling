#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# 07_docker_build_up.sh — build images and bring the full stack
# up, waiting for db/web healthchecks before declaring success.
#
# Also gives you a rollback path: before building a new image,
# whatever was running as chuka_web:latest gets re-tagged
# chuka_web:previous. If the new build turns out bad, run
# scripts/rollback.sh to instantly point back at it.
# Every build/rollback is also appended to logs/deploy_history.log
# for a basic audit trail.
# ──────────────────────────────────────────────────────────────

docker_build_and_up() {
  step "9/11  Building and starting the Docker stack"

  cd "$PROJECT_ROOT"
  local IMAGE_NAME="chuka_web"

  # ---- Preserve the currently-running image as a rollback point ----------
  if docker image inspect "${IMAGE_NAME}:latest" >/dev/null 2>&1; then
    info "Tagging current image as ${IMAGE_NAME}:previous (rollback point)..."
    docker tag "${IMAGE_NAME}:latest" "${IMAGE_NAME}:previous"
  else
    info "No existing ${IMAGE_NAME}:latest image found — first deploy, nothing to preserve."
  fi

  info "Building images (this can take a few minutes on first run)..."
  docker compose build || fail "docker compose build failed — see the output above for the failing step (Dockerfile error, network pull failure, disk space, etc.)."

  # ---- Also keep a timestamped/commit-tagged copy for history -------------
  local build_tag
  if git -C "$PROJECT_ROOT" rev-parse --short HEAD >/dev/null 2>&1; then
    build_tag="$(git -C "$PROJECT_ROOT" rev-parse --short HEAD)_$(date +%Y%m%d%H%M%S)"
  else
    build_tag="$(date +%Y%m%d%H%M%S)"
  fi
  docker tag "${IMAGE_NAME}:latest" "${IMAGE_NAME}:${build_tag}" 2>/dev/null || true

  mkdir -p "$PROJECT_ROOT/logs"
  echo "$(date -Iseconds)  DEPLOY   tag=${build_tag}  by=$(whoami)  host=$(hostname)" >> "$PROJECT_ROOT/logs/deploy_history.log"

  info "Starting services..."
  if ! docker compose up -d; then
    warn "docker compose up failed to start the containers. Recent logs from each service:"
    docker compose logs --tail=30

    warn "A common cause on repeated failed attempts is a database volume left"
    warn "half-initialized by an earlier crashed run (e.g. a bad DB_USER value)."
    warn "Resetting it deletes ALL data currently in the db volume."
    if confirm "Reset the db_data volume and retry the deploy once?"; then
      info "Resetting stack (docker compose down -v) and retrying..."
      docker compose down -v
      docker compose up -d || fail "docker compose up failed again after resetting the db volume — this is no longer a stale-volume issue, check the logs above (bad .env value, port conflict, etc.)."
    else
      fail "docker compose up failed to even start the containers — see the output above (often a port conflict, bad .env value, or volume/mount permission issue)."
    fi
  fi

  info "Waiting for containers to become healthy..."
  # Must exceed the web healthcheck's own worst-case window in docker-compose.yml
  # (start_period + retries*interval) so this loop never gives up before Docker
  # itself would. A fresh DB with a full first-time migration run can take
  # several minutes, so this is generous on purpose.
  local max_wait=420 waited=0 all_healthy=false

  while (( waited < max_wait )); do
    local statuses
    statuses=$(docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null || true)

    # db and web declare healthchecks; nginx does not, so just check "running"
    local db_ok web_ok nginx_running
    db_ok=$(echo "$statuses"   | awk '$1=="db"    {print $2}')
    web_ok=$(echo "$statuses"  | awk '$1=="web"   {print $2}')
    nginx_running=$(docker compose ps nginx --format '{{.State}}' 2>/dev/null || true)

    if [[ "$db_ok" == "healthy" && "$web_ok" == "healthy" && "$nginx_running" == "running" ]]; then
      all_healthy=true
      break
    fi

    sleep 5
    waited=$((waited + 5))
    info "  ... still waiting ($waited/${max_wait}s) [db=$db_ok web=$web_ok nginx=$nginx_running]"
  done

  if ! $all_healthy; then
    warn "Stack did not report healthy within ${max_wait}s. Recent logs:"
    docker compose logs --tail=40
    echo "$(date -Iseconds)  FAILED   tag=${build_tag}  by=$(whoami)  host=$(hostname)" >> "$PROJECT_ROOT/logs/deploy_history.log"
    if docker image inspect "${IMAGE_NAME}:previous" >/dev/null 2>&1; then
      fail "Deployment did not come up cleanly. Previous image is preserved as ${IMAGE_NAME}:previous — run ./scripts/rollback.sh to revert, or fix the issue above and re-run this deploy."
    else
      fail "Deployment did not come up cleanly, and there was no previous image to roll back to (this was likely the first deploy). Fix the issue above and re-run."
    fi
  fi

  ok "All containers are up and healthy (image tag: ${build_tag})."
  docker compose ps
}
