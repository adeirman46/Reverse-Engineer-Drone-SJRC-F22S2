#!/usr/bin/env bash
# Renders every screen of the interface (demo data) with headless Chrome into screenshots/<size>/.
# Usage: tools/ui_screenshots.sh            (needs google-chrome or chromium on PATH)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI="file://$ROOT/app/src/main/assets/ui/index.html"
OUT="$ROOT/screenshots"
CHROME="$(command -v google-chrome || command -v chromium || command -v chromium-browser)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

SCREENS=(
  "01-fly-video-sticks|"
  "02-fly-sticks-in-use|touch=1"
  "03-fly-map-main|main=map"
  "04-fly-map-tap-fly-here|main=map&menu=1"
  "05-fly-ready-on-ground|armed=0"
  "06-fly-altitude-mode-indoor|armed=0&indoor=1"
  "07-fly-recording|rec=1"
  "08-fly-warnings|warn=1"
  "09-fly-without-sticks|sticks=0"
  "10-confirm-take-off|armed=0&confirm=fly"
  "11-confirm-land|confirm=fly"
  "12-confirm-return-home|confirm=rth"
  "13-plan-mission|view=plan&plan=demo"
  "14-plan-waypoint-selected|view=plan&plan=demo&sel=2"
  "15-plan-confirm-mission|view=plan&plan=demo&confirm=mission"
  "16-plan-uploading|view=plan&plan=demo&mission=uploading"
  "17-fly-mission-running|main=map&plan=demo&mission=running"
  "18-connect-plug-in-remote|demo=connect"
  "19-connect-replug-for-safety|demo=replug"
  "20-connect-ready|demo=plugged"
  "21-settings|panel=settings"
  "22-waiting-for-video|armed=0&novideo=1"
  "23-flight-mode-gps|modes=1"
  "24-flight-mode-attitude-indoor|armed=0&indoor=1&modes=1"
  "25-plan-drone-without-gps|view=plan&plan=demo&indoor=1&armed=0"
  "26-map-search|main=map&search=Monas Jakarta"
  "27-photo-saved-to-gallery|saved=1&rec=1"
  "28-settings-flight-limits|panel=settings&scroll=limits"
  "29-confirm-flight-limits|confirm=limits"
  "30-confirm-take-off-with-warning|armed=0&warn=1&confirm=fly"
  "31-take-off-refused-reason|armed=0&warn=1&alert=1"
  "32-no-fly-block-left-by-official-app|panel=settings&nofly=1&armed=0&scroll=limits"
  "33-plan-long-mission|view=plan&plan=long"
  "34-plan-confirm-long-mission|view=plan&plan=long&confirm=mission"
)
SIZES=("phone|873,393" "tablet|1280,800")

shot() {  # name query size-name window
  local prof="$TMP/$3-$1"
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --user-data-dir="$prof" \
    --window-size="$4" --force-device-scale-factor=2 --virtual-time-budget=9000 \
    --screenshot="$OUT/$3/$1.png" "$UI?$2" >/dev/null 2>&1 && echo "  $3/$1.png"
}

for s in "${SIZES[@]}"; do rm -rf "$OUT/${s%%|*}"; mkdir -p "$OUT/${s%%|*}"; done
n=0
for s in "${SIZES[@]}"; do
  for e in "${SCREENS[@]}"; do
    shot "${e%%|*}" "${e#*|}" "${s%%|*}" "${s#*|}" &
    n=$((n + 1))
    if (( n % 4 == 0 )); then wait; fi
  done
done
wait
echo "Screenshots in $OUT"
