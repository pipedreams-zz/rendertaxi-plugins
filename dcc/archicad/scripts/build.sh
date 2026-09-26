#!/usr/bin/env bash
#
# Ein Befehl baut das `.bundle`.
#
#   integrations/archicad/addon/scripts/build.sh
#
# Voraussetzung ist das Archicad 28 API Development Kit. Sein Pfad kommt aus
# `AC_API_DEVKIT_DIR`; ohne DevKit baut das Skript nur den DevKit-freien Kern
# und sagt das ausdrücklich, statt stillschweigend etwas anderes zu tun.

source "$(dirname "$0")/common.sh"

GENERATOR=(-G Xcode)
CMAKE_ARGS=(-S "${ADDON_DIR}" -B "${BUILD_DIR}" "${GENERATOR[@]}" -DRTX_BUILD_TESTS=ON)

if [ -f "${DEVKIT}/Support/Inc/ACAPinc.h" ]; then
	say "DevKit: ${DEVKIT}"
	CMAKE_ARGS+=(-DAC_API_DEVKIT_DIR="${DEVKIT}")
	TARGETS=(--target AddOn)
else
	say "Kein DevKit unter ${DEVKIT} — es wird nur der DevKit-freie Kern gebaut."
	say "Setze AC_API_DEVKIT_DIR, um das Bundle zu bauen."
	CMAKE_ARGS+=(-DRTX_BUILD_ADDON=OFF)
	TARGETS=()
fi

cmake "${CMAKE_ARGS[@]}"
cmake --build "${BUILD_DIR}" --config "${CONFIG}" "${TARGETS[@]}"

BUNDLE="${BUILD_DIR}/${CONFIG}/${ADDON_NAME}.bundle"
if [ -d "${BUNDLE}" ]; then
	say ""
	say "Gebaut: ${BUNDLE}"
	say "Installieren: ${ADDON_DIR}/scripts/install.sh"
fi
