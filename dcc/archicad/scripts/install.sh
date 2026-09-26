#!/usr/bin/env bash
#
# Ein Befehl installiert das gebaute `.bundle` in den Add-On-Ordner von
# Archicad 28.
#
#   integrations/archicad/addon/scripts/install.sh
#
# Archicad lädt Add-Ons beim Start. Läuft Archicad, muss es danach neu
# gestartet werden; das Skript sagt das.

source "$(dirname "$0")/common.sh"

BUNDLE="${BUILD_DIR}/${CONFIG}/${ADDON_NAME}.bundle"
[ -d "${BUNDLE}" ] || die "Kein gebautes Bundle unter ${BUNDLE}. Erst scripts/build.sh ausführen."
[ -d "${ARCHICAD_DIR}" ] || die "Archicad 28 nicht unter ${ARCHICAD_DIR}. RTX_ARCHICAD_DIR setzen."

TARGET_DIR="${ARCHICAD_DIR}/Add-Ons/rendertaxi"
mkdir -p "${TARGET_DIR}"
rm -rf "${TARGET_DIR}/${ADDON_NAME}.bundle"
cp -R "${BUNDLE}" "${TARGET_DIR}/"

# Ein kopiertes Bundle verliert unter macOS die Signatur des Buildlaufs; ohne
# gültige Ad-hoc-Signatur verweigert das System das Laden.
codesign --force --deep --sign - "${TARGET_DIR}/${ADDON_NAME}.bundle" >/dev/null 2>&1 || true

say "Installiert: ${TARGET_DIR}/${ADDON_NAME}.bundle"
if pgrep -x "Archicad 28" >/dev/null 2>&1 || pgrep -f "Archicad 28.app" >/dev/null 2>&1; then
	say "Archicad läuft — bitte neu starten, damit das Add-On geladen wird."
fi
