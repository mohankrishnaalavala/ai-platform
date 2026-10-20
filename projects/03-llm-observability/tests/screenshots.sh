#!/usr/bin/env bash
# Grafana screenshots of P3's dashboards under the game day's background load (session 2, decision 15): the
# runner's headless Chrome through a port-forward. Grafana has anonymous viewers only, so nothing logs in.
# Check every screenshot for secrets, project IDs and emails before it goes into a post.
# Usage (from the repo root, layers 01-03 up): ZONE=... OUT=dir [WARMUP_S=180] screenshots.sh
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
out=${OUT:?set OUT}
mkdir -p "$out"
# shellcheck source=/dev/null  # lib.sh, next to this script
source "$here/lib.sh"
load=''
trap '[ -z "$load" ] || kill "$load" 2>/dev/null || true; stop_port_forwards' EXIT
port_forward monitoring/prometheus-grafana 13000:80

# A few minutes of the game day's load first, so the panels' 5-minute rates have points.
GAMEDAY_MAX_S=900 python3 "$here/gameday/load.py" > "$out/screenshot-load.txt" 2>&1 & load=$!
sleep "${WARMUP_S:-180}"
chrome=$(command -v google-chrome || command -v chromium) || { echo "no Chrome on this machine"; exit 1; }
for f in "$here"/../dashboards/*.json; do
  uid=$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["uid"])' "$f")
  # --no-sandbox: Ubuntu 24.04 runners block the user namespaces Chrome's sandbox needs; the page is our own.
  "$chrome" --headless=new --no-sandbox --disable-gpu --hide-scrollbars --window-size=1600,1200 \
    --virtual-time-budget=20000 --screenshot="$out/$uid.png" \
    "http://127.0.0.1:13000/d/$uid?orgId=1&kiosk&from=now-15m&to=now" >> "$out/chrome.txt" 2>&1
  echo "screenshot: $uid.png ($(wc -c < "$out/$uid.png") bytes)"
done
