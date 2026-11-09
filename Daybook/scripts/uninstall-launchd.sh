#!/usr/bin/env bash
# Stop the background Daybook service and remove it from login items.
set -euo pipefail
LABEL="com.daybook.server"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "Daybook background service removed. Your data is untouched."
