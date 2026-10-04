#!/usr/bin/env bash
# Bleshtastic device provisioning over USB.
#
# Usage:  ./provision.sh <tier> <city-code> <serial> [port]
#   tier = block | cookout | kitchen   (each tier includes the ones below it)
#   e.g.  ./provision.sh block   SLA 0042      # a device for the public
#         ./provision.sh kitchen SLA 0001      # your own / close friends
#
# Requires: pip install meshtastic segno, then run gen_channels.py once.
set -euo pipefail

TIER="${1:?tier: block | cookout | kitchen}"
CITY="${2:?city code, e.g. SLA}"
SERIAL="${3:?serial number, e.g. 0042}"
PORT_ARG=()
[[ -n "${4:-}" ]] && PORT_ARG=(--port "$4")
case "$TIER" in block|cookout|kitchen) ;; *) echo "tier must be block, cookout or kitchen" >&2; exit 1;; esac

HERE="$(cd "$(dirname "$0")" && pwd)"
[[ -f "$HERE/out/links.txt" ]] || python3 "$HERE/gen_channels.py" >/dev/null
URL="$(grep -A1 "through ${TIER^^}" "$HERE/out/links.txt" | tail -1)"

m() { meshtastic "${PORT_ARG[@]}" "$@"; sleep 4; }   # radios reboot after some writes

echo "== Bleshtastic: ${CITY}-${SERIAL} (${TIER})"
m --set-owner "Blesh ${CITY}-${SERIAL}" --set-owner-short "B${SERIAL: -3}"
m --seturl "$URL"                                   # channels + US / LongFast / hop 3
m --set position.gps_mode DISABLED --set position.position_broadcast_smart_enabled false

mkdir -p "$HERE/inventory"
meshtastic "${PORT_ARG[@]}" --info > "$HERE/inventory/${CITY}-${SERIAL}.txt"
echo "${CITY}-${SERIAL},${TIER},$(date -u +%FT%TZ)" >> "$HERE/inventory/log.csv"
echo "== Done. Label it ${CITY}-${SERIAL}, add the setup card, box it."
