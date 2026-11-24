#!/usr/bin/env bash
# Restart only a cloud service whose own Docker health check reports unhealthy.
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE=(docker compose -f "$PROJECT/docker-compose.cloud.yml")

for service in crypto stocks dashboard; do
  container_id="$("${COMPOSE[@]}" ps -q "$service" 2>/dev/null || true)"
  [ -n "$container_id" ] || continue
  health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_id" 2>/dev/null || true)"
  if [ "$health" = "unhealthy" ]; then
    logger -t paperbot-watchdog "Restarting unhealthy $service service"
    "${COMPOSE[@]}" restart "$service"
  fi
done
