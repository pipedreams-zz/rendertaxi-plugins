#!/usr/bin/env bash
#
# Legt die Binärdateien für die Auslieferung nach `dist/` (Muster: gisloader,
# dcc/archicad/dist/):
#
#   dist/macos/rendertaxi.bundle   auf diesem Mac mit scripts/build.sh gebaut
#   dist/win/rendertaxi.apx        aus dem letzten grünen Lauf von
#                                  .github/workflows/archicad-windows.yml;
#                                  nicht eingecheckt, nur für ein lokales
#                                  scripts/pack.sh (der Release baut sie selbst)
#
#   integrations/archicad/addon/scripts/dist.sh            # beides
#   integrations/archicad/addon/scripts/dist.sh macos      # nur das Bundle
#   integrations/archicad/addon/scripts/dist.sh win [RUN]  # nur die .apx
#
# Das Windows-Artefakt holt `gh run download`; ohne Lauf-ID der letzte
# erfolgreiche Lauf auf `main`. `scripts/pack.sh` im öffentlichen Spiegel packt
# genau diesen Ordner.

source "$(dirname "$0")/common.sh"

DIST_DIR="${ADDON_DIR}/dist"
WHAT="${1:-all}"

dist_macos () {
	local bundle="${BUILD_DIR}/${CONFIG}/${ADDON_NAME}.bundle"
	[ -d "${bundle}" ] || die "Kein gebautes Bundle unter ${bundle}. Erst scripts/build.sh ausführen."
	rm -rf "${DIST_DIR}/macos"
	mkdir -p "${DIST_DIR}/macos"
	cp -R "${bundle}" "${DIST_DIR}/macos/"
	say "macOS: ${DIST_DIR}/macos/${ADDON_NAME}.bundle ($(lipo -archs "${DIST_DIR}/macos/${ADDON_NAME}.bundle/Contents/MacOS/${ADDON_NAME}"))"
}

dist_win () {
	local run="${1:-}"
	command -v gh >/dev/null 2>&1 || die "gh (GitHub CLI) fehlt."
	if [ -z "${run}" ]; then
		run="$(gh run list --workflow archicad-windows.yml --branch main --status success \
			--limit 1 --json databaseId --jq '.[0].databaseId')"
		[ -n "${run}" ] || die "Kein erfolgreicher Lauf von archicad-windows.yml auf main."
	fi
	local tmp
	tmp="$(mktemp -d)"
	gh run download "${run}" --name "${ADDON_NAME}-archicad-windows" --dir "${tmp}"
	rm -rf "${DIST_DIR}/win"
	mkdir -p "${DIST_DIR}/win"
	cp "${tmp}/${ADDON_NAME}.apx" "${DIST_DIR}/win/"
	rm -rf "${tmp}"
	say "Windows: ${DIST_DIR}/win/${ADDON_NAME}.apx (Lauf ${run})"
}

case "${WHAT}" in
	all) dist_macos; dist_win ;;
	macos) dist_macos ;;
	win) dist_win "${2:-}" ;;
	*) die "Unbekannt: ${WHAT} (all, macos, win)" ;;
esac
