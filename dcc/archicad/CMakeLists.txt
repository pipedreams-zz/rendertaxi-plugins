# rendertaxi-Add-on für Archicad 28 — Wurzelprojekt.
#
# Zwei Ziele mit verschiedenen Voraussetzungen:
#
#   rtxcore        DevKit-frei. Manifest, Kanonisierung, HTTP, Tokenablage,
#                  Uploader und Zustandsautomat. Baut und testet auf jedem Mac,
#                  ohne Archicad und ohne DevKit.
#   rendertaxi     Das `.bundle`. Braucht das Archicad 28 API Development Kit;
#                  der Pfad kommt aus `AC_API_DEVKIT_DIR` oder der gleichnamigen
#                  Umgebungsvariablen. Das DevKit gehört nicht ins Repository.
#
# Liegt kein DevKit vor, konfiguriert das Projekt trotzdem und baut den Kern.
# Das ist die Trennung aus `docs/development-environments.md`, Abschnitt 4.
#
#   cmake -S . -B build -G Xcode -DRTX_BUILD_TESTS=ON
#   cmake --build build --config Release
#
# Bequemer sind die beiden Skripte: `scripts/build.sh` und `scripts/test.sh`.

cmake_minimum_required (VERSION 3.16)

project (rendertaxi-archicad CXX)

set (CMAKE_CXX_STANDARD 17)
set (CMAKE_CXX_STANDARD_REQUIRED ON)
set_property (GLOBAL PROPERTY USE_FOLDERS ON)
set (CMAKE_SUPPRESS_REGENERATION 1)
set (CMAKE_CONFIGURATION_TYPES Debug;Release;RelWithDebInfo)

option (RTX_BUILD_TESTS "Tests des DevKit-freien Kerns bauen" ON)
option (RTX_BUILD_ADDON "Das Archicad-Bundle bauen, wenn ein DevKit gefunden wird" ON)

if (APPLE)
	# Archicad 28 für macOS ist gegen macOS 13.0 gebaut; Graphisoft gibt diese
	# Zuordnung je Version vor (`docs/development-environments.md`, Abschnitt 1).
	set (CMAKE_OSX_DEPLOYMENT_TARGET 13.0)
	if (CMAKE_GENERATOR STREQUAL Xcode)
		set (CMAKE_OSX_ARCHITECTURES "$(ARCHS_STANDARD)" CACHE INTERNAL "" FORCE)
		set (CMAKE_XCODE_ATTRIBUTE_ONLY_ACTIVE_ARCH "NO")
	endif ()
endif ()

add_subdirectory (core)

# --- Das DevKit-abhängige Bundle ---------------------------------------------

if (NOT DEFINED AC_API_DEVKIT_DIR AND DEFINED ENV{AC_API_DEVKIT_DIR})
	set (AC_API_DEVKIT_DIR "$ENV{AC_API_DEVKIT_DIR}" CACHE PATH "Archicad API DevKit.")
endif ()

set (RTX_ADDON_BUILDABLE OFF)
if (RTX_BUILD_ADDON AND DEFINED AC_API_DEVKIT_DIR AND
	EXISTS "${AC_API_DEVKIT_DIR}/Support/Inc/ACAPinc.h")
	set (RTX_ADDON_BUILDABLE ON)
endif ()

if (RTX_ADDON_BUILDABLE)
	include ("${CMAKE_CURRENT_LIST_DIR}/cmake/Addon.cmake")
else ()
	message (STATUS
		"Kein Archicad DevKit unter AC_API_DEVKIT_DIR — es wird nur der DevKit-freie Kern "
		"gebaut. Das ist der erwartete Zustand auf einer Maschine ohne Archicad.")
endif ()
