#!/usr/bin/env bash
# Install boot startup and a host timer that restarts only unhealthy services.
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_UNIT=/etc/systemd/system/paperbot-compose.service
WATCHDOG_UNIT=/etc/systemd/system/paperbot-watchdog.service
TIMER_UNIT=/etc/systemd/system/paperbot-watchdog.timer

sed "s|@PROJECT@|$PROJECT|g" "$PROJECT/deploy/cloud/paperbot-compose.service.template" \
  | sudo tee "$COMPOSE_UNIT" >/dev/null

sudo tee "$WATCHDOG_UNIT" >/dev/null <<UNIT
[Unit]
Description=Restart unhealthy paper trading services independently

[Service]
Type=oneshot
WorkingDirectory=$PROJECT
ExecStart=$PROJECT/deploy/cloud/watchdog.sh
UNIT

sudo tee "$TIMER" >/dev/null <<'UNIT'
[Unit]
Description=Check paper trading service health every minute

[Timer]
OnBootSec=2min
OnUnitActiveSec=30s
Persistent=true

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now docker.service paperbot-compose.service paperbot-watchdog.timer
echo "Boot startup and watchdog installed. Check with: systemctl status paperbot-compose.service paperbot-watchdog.timer"
