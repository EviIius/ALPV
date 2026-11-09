#!/usr/bin/env bash
# Run Daybook in the background on macOS: starts at login and restarts if it
# stops.
#   scripts/install-launchd.sh                       # port 8000, reachable on LAN + Tailscale
#   scripts/install-launchd.sh 8000 --localhost-only # only via `tailscale serve` (HTTPS) or this Mac
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-8000}"
shift || true
EXTRA=""
for arg in "$@"; do EXTRA="$EXTRA    <string>$arg</string>
"; done
LABEL="com.daybook.server"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOGDIR="$HOME/Library/Logs/Daybook"

if [ ! -x "$REPO/.venv/bin/python" ]; then
  echo "Run ./start.sh once first (it creates .venv and installs dependencies), then press Ctrl+C and rerun this script." >&2
  exit 1
fi
mkdir -p "$HOME/Library/LaunchAgents" "$LOGDIR"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$REPO/.venv/bin/python</string>
    <string>$REPO/run.py</string>
    <string>--port</string><string>$PORT</string>
$EXTRA  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StandardOutPath</key><string>$LOGDIR/daybook.log</string>
  <key>StandardErrorPath</key><string>$LOGDIR/daybook.log</string>
</dict>
</plist>
PLISTEOF

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"
sleep 2
echo "Daybook is running in the background on port $PORT."
echo "  Open:  http://localhost:$PORT"
echo "  Logs:  $LOGDIR/daybook.log   (the first-run setup code is printed there)"
echo "  Stop:  scripts/uninstall-launchd.sh"
tail -n 15 "$LOGDIR/daybook.log" 2>/dev/null || true
