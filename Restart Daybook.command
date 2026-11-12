#!/usr/bin/env bash
# Double-click in Finder to restart Daybook: stops the copy that's running and starts the files
# that are in this folder now, with the same options.
# (Updates can also be finished from the app itself: "Update ready" → Restart now, or Settings → About.)
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$DIR"; [ -f "$DIR/Daybook/run.py" ] && APP="$DIR/Daybook"
cd "$APP" || exit 1
PORT="${PORT:-8000}"
ARGS=""
stopped=0
for pid in $(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null); do
  cmd="$(ps -o command= -p "$pid" 2>/dev/null)"
  case "$cmd" in
    *run.py*)
      echo "Stopping the running Daybook (process $pid)…"
      ARGS="${cmd#*run.py}"
      kill "$pid" 2>/dev/null
      stopped=1 ;;
    *)
      echo "Port $PORT is being used by something that isn't Daybook:"
      echo "  $cmd"
      echo "Leaving it alone."
      read -r -p "Press Enter to close" _
      exit 1 ;;
  esac
done
if [ "$stopped" = 1 ]; then
  for _ in $(seq 1 60); do
    lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.25
  done
  sleep 1
  # Kept running in the background (launchd)? Then it has already come back with the new files.
  if lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "Daybook restarted."
    sleep 2
    exit 0
  fi
fi
# shellcheck disable=SC2086
exec ./start.sh $ARGS
