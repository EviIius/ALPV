#!/bin/bash
# Puts Daybook on its own Tailscale HTTPS port, leaving Aether on 443 untouched.
TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
if [ ! -x "$TS" ]; then echo "Tailscale CLI not found."; read -p "Press Enter to close"; exit 1; fi
echo "== Current Tailscale serve setup =="
"$TS" serve status
PORT=8443
if "$TS" serve status 2>/dev/null | grep -q ":8443" && ! "$TS" serve status 2>/dev/null | grep -A2 ":8443" | grep -q "127.0.0.1:8000"; then
  PORT=10000
fi
echo; echo "== Adding Daybook on port $PORT =="
"$TS" serve --bg --https=$PORT 8000
echo; echo "== New setup =="
"$TS" serve status
HOST=$("$TS" status --json 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null)
echo; echo "Daybook on your phone:  https://${HOST:-jakes-mac-mini.tailc4d343.ts.net}:$PORT"
echo "(Daybook itself must be running — use Start Daybook.command.)"
read -p "Press Enter to close"
