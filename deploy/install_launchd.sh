#!/usr/bin/env bash
# macOS: keep the paper bot running in the background (starts at login, restarts on crash).
#   ./deploy/install_launchd.sh            install + start
#   ./deploy/install_launchd.sh uninstall  stop + remove
set -euo pipefail
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL=com.charles.cryptopaperbot
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
if [ "${1:-}" = "uninstall" ]; then
  launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || true
  rm -f "$DEST"; echo "Removed $LABEL"; exit 0
fi
[ -x "$PROJECT/.venv/bin/python" ] || { echo "Create the venv first (see README)"; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/data"
sed "s#__PROJECT__#$PROJECT#g" "$PROJECT/deploy/$LABEL.plist.template" > "$DEST"
launchctl bootout "gui/$(id -u)" "$DEST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "Installed and started $LABEL. Logs: $PROJECT/data/bot.log"
echo "Check: launchctl print gui/$(id -u)/$LABEL | head"
