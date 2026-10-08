#!/usr/bin/env bash
# Prüft, dass das eingecheckte macOS-Bundle aus dem Quellstand gebaut wurde:
# Version aus src/Version.hpp (die eine Quelle) muss in Info.plist und im
# Programm des Bundles stehen. Verhindert, dass ein altes Bundle unter dem
# Dateinamen einer neueren Version ausgeliefert wird.
#
#   bash scripts/check-archicad-bundle.sh      # im Spiegel (dcc/archicad/)
set -euo pipefail
cd "$(dirname "$0")/.."
ADDON="${ARCHICAD_DIR:-dcc/archicad}"
VER=$(grep -oE 'RTX_ADDON_VERSION "[0-9]+\.[0-9]+\.[0-9]+"' "$ADDON/src/Version.hpp" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
test -n "$VER" || { echo "Archicad: Version in Version.hpp nicht lesbar" >&2; exit 1; }
B="$ADDON/dist/macos/rendertaxi.bundle/Contents"
test -f "$B/Info.plist" -a -f "$B/MacOS/rendertaxi" || { echo "Fehlt: macOS-Bundle unter $ADDON/dist/macos/" >&2; exit 1; }
grep -q "Archicad [0-9]*, $VER</string>" "$B/Info.plist" \
  || { echo "macOS-Bundle meldet in Info.plist nicht $VER — mit scripts/dist.sh macos am Mac neu bauen und einchecken" >&2; exit 1; }
grep -aqF "$VER" "$B/MacOS/rendertaxi" \
  || { echo "macOS-Bundle enthält die Version $VER nicht im Programm — am Mac neu bauen" >&2; exit 1; }
echo "macOS-Bundle ist $VER"
