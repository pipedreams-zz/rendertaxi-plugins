#!/usr/bin/env bash
# Packt jedes vorhandene Plugin als Zip nach dist/ und schreibt dist/RELEASE.md
# mit Version und Kurzanleitung (Muster: gisloader-plugins/scripts/pack.sh).
#
#   bash scripts/pack.sh [RELEASE]
#
# Dateinamen tragen Hostprogramm, Hostversion, Plugin-Version und
# Release-Nummer, damit Nutzer Stände unterscheiden können:
#   rendertaxi-archicad28-1.0.0-2026.09.26.zip
#   rendertaxi-blender5.2-0.1.0-2026.09.26.zip
# RELEASE ist das Tag des Releases (der Release-Workflow übergibt es); ohne
# Angabe das heutige Datum.
#
# Blender: die Zip **ist** die Extension (blender_manifest.toml an der Wurzel),
# installierbar über „Install from Disk"; Version und Mindestversion kommen aus
# dcc/blender/blender_manifest.toml.
#
# Archicad: dcc/archicad/dist/ muss macos/rendertaxi.bundle **und**
# win/rendertaxi.apx enthalten; fehlt eines, bricht das Skript ab, statt ein
# halbes Paket auszuliefern. Die .apx legt der Release-Workflow aus seinem
# eigenen Windows-Build dorthin; lokal holt sie
# dcc/archicad/scripts/dist.sh win.
set -euo pipefail
cd "$(dirname "$0")/.."
RELEASE="${1:-$(date -u +%Y.%m.%d)}"
mkdir -p dist
rm -f dist/*.zip dist/RELEASE.md

# Die eine Quelle der Plugin-Version ist src/Version.hpp des Add-ons; die
# Archicad-Hauptversion steht in der Versionsprüfung von cmake/Addon.cmake.
ver () { grep -oE "$2" "$1" | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "?"; }
ARCHICAD=$(ver dcc/archicad/src/Version.hpp 'RTX_ADDON_VERSION "[^"]+"')
ARCHICAD_HOST=$(grep -oE 'ARCHICAD_VERSION STREQUAL "[0-9]+"' dcc/archicad/cmake/Addon.cmake | grep -oE '[0-9]+')
test "$ARCHICAD" != "?" -a -n "$ARCHICAD_HOST" || { echo "Version oder Hostversion nicht lesbar" >&2; exit 1; }
ARCHICAD_ZIP="rendertaxi-archicad${ARCHICAD_HOST}-${ARCHICAD}-${RELEASE}.zip"

ARCHICAD_DIST=dcc/archicad/dist
test -d "$ARCHICAD_DIST/macos/rendertaxi.bundle" || { echo "Fehlt: $ARCHICAD_DIST/macos/rendertaxi.bundle" >&2; exit 1; }
test -f "$ARCHICAD_DIST/win/rendertaxi.apx" || { echo "Fehlt: $ARCHICAD_DIST/win/rendertaxi.apx" >&2; exit 1; }
cp docs/dcc/archicad.md "$ARCHICAD_DIST/ANLEITUNG.md"
(cd "$ARCHICAD_DIST" && zip -qry "../../../dist/$ARCHICAD_ZIP" ANLEITUNG.md macos win -x '*.DS_Store')
rm -f "$ARCHICAD_DIST/ANLEITUNG.md"

# Blender: Version und Mindestversion (MAJOR.MINOR) aus blender_manifest.toml.
BLENDER_MANIFEST=dcc/blender/blender_manifest.toml
test -f "$BLENDER_MANIFEST" || { echo "Fehlt: $BLENDER_MANIFEST" >&2; exit 1; }
BLENDER=$(grep -E '^version = "[0-9]+\.[0-9]+\.[0-9]+"' "$BLENDER_MANIFEST" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
BLENDER_HOST=$(grep -E '^blender_version_min = ' "$BLENDER_MANIFEST" | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)
test -n "$BLENDER" -a -n "$BLENDER_HOST" || { echo "Blender: Version oder Mindestversion nicht lesbar" >&2; exit 1; }
BLENDER_ZIP="rendertaxi-blender${BLENDER_HOST}-${BLENDER}-${RELEASE}.zip"
(cd dcc/blender && zip -qr "../../dist/$BLENDER_ZIP" . -x '*__pycache__*' -x '*.DS_Store' -x '.gitignore')
unzip -l "dist/$BLENDER_ZIP" | grep -q ' blender_manifest.toml$' || { echo "Blender-Zip ohne blender_manifest.toml an der Wurzel" >&2; exit 1; }

cat > dist/RELEASE.md <<EOF
Release $RELEASE

| Plugin | Hostprogramm | Version | Datei |
| --- | --- | --- | --- |
| Archicad | Archicad $ARCHICAD_HOST (macOS, Windows) | $ARCHICAD | $ARCHICAD_ZIP (macOS-Bundle und Windows-.apx) |
| Blender | Blender $BLENDER_HOST LTS (macOS, Windows, Linux) | $BLENDER | $BLENDER_ZIP (Extension, Vorschau) |

**Archicad installieren:** Zip entpacken, Archicad beenden.

- macOS: \`macos/rendertaxi.bundle\` nach \`/Applications/Graphisoft/Archicad 28/Add-Ons/\` kopieren. Das Bundle ist nicht notarisiert; meldet macOS beim Start eine Sperre, einmal \`xattr -dr com.apple.quarantine "/Applications/Graphisoft/Archicad 28/Add-Ons/rendertaxi.bundle"\` im Terminal ausführen.
- Windows: \`win/rendertaxi.apx\` nach \`C:\\Program Files\\Graphisoft\\Archicad 28\\Add-Ons\\\` kopieren. Vorher die Zip freigeben (Rechtsklick › Eigenschaften › Zulassen), sie ist nicht signiert. Die Windows-Fassung ist eine Vorschau, siehe Anleitung.

Danach Archicad starten, Menü **rendertaxi.ai › Palette**. Vollständige Anleitung: [docs/dcc/archicad.md](docs/dcc/archicad.md) (liegt auch als ANLEITUNG.md in der Zip).

**Blender installieren:** Zip **nicht** entpacken. In Blender **Edit › Preferences › Get Extensions ›** Menü oben rechts **› Install from Disk…** und die Zip wählen; **Allow Online Access** unter **System › Network** einschalten. Danach in der 3D-Ansicht **N › rendertaxi**. Vollständige Anleitung: [docs/dcc/blender.md](docs/dcc/blender.md).
EOF
ls -l dist
unzip -l "dist/$ARCHICAD_ZIP" | tail -n +1
unzip -l "dist/$BLENDER_ZIP" | tail -n +1
cat dist/RELEASE.md
