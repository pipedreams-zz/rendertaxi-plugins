#!/usr/bin/env bash
# Packt jedes vorhandene Plugin als Zip nach dist/ und schreibt dist/RELEASE.md
# mit Version und Kurzanleitung (Muster: gisloader-plugins/scripts/pack.sh).
#
#   bash scripts/pack.sh [RELEASE]
#
# Dateinamen tragen Hostprogramm, Hostversion, Plugin-Version und
# Release-Nummer, damit Nutzer Stände unterscheiden können:
#   rendertaxi-archicad28-1.0.0-2026.09.26.zip
#   rendertaxi-blender4.5-0.5.0-2026.10.05.zip   (Zahl nach „blender" = Mindestversion)
#   rendertaxi-cinema4d2026-0.1.0-2026.09.30.zip
#   rdtx.ai-0.1.0+3339-rh8-any.yak               (Rhino: Name, wie rhinocode ihn vergibt)
# RELEASE ist das Tag des Releases (der Release-Workflow übergibt es); ohne
# Angabe das heutige Datum.
#
# Blender: die Zip **ist** die Extension (blender_manifest.toml an der Wurzel),
# installierbar über „Install from Disk"; Version und Mindestversion kommen aus
# dcc/blender/blender_manifest.toml.
#
# Cinema 4D: die Zip enthält einen Ordner rendertaxi/ (rendertaxi.pyp, das
# Paket rendertaxi_c4d/ und ANLEITUNG.md), den Nutzer in den Plugin-Ordner von
# Cinema 4D kopieren; Version und Hostfassung kommen aus
# dcc/cinema4d/rendertaxi_c4d/host.py.
#
# Rhino: das yak-Paket aus dcc/rhino/dist/ (gebaut am Mac mit rhinocode,
# integrations/rhino/scripts/pack.sh im Plattform-Repository) geht unverändert
# ins Release — der Rhino Package Manager installiert es per Ziehen ins
# Rhino-Fenster oder über _PackageManager. Version aus
# dcc/rhino/lib/rendertaxi_rhino/host.py; das Paket muss dieselbe Version tragen.
#
# Blender, Cinema 4D und Rhino tragen den gemeinsamen Python-Client
# (rendertaxi_client/) eingebettet; fehlt er, bricht das Skript ab.
#
# Archicad: dcc/archicad/dist/ muss macos/rendertaxi.bundle **und**
# win/rendertaxi.apx enthalten und das Bundle muss die Version aus Version.hpp
# tragen (scripts/check-archicad-bundle.sh); fehlt eines, bricht das Skript ab, statt ein
# halbes Paket auszuliefern. Die .apx legt der Release-Workflow aus seinem
# eigenen Windows-Build dorthin; lokal holt sie
# dcc/archicad/scripts/dist.sh win.
set -euo pipefail
cd "$(dirname "$0")/.."
RELEASE="${1:-$(date -u +%Y.%m.%d)}"
mkdir -p dist
rm -f dist/*.zip dist/*.yak dist/RELEASE.md

# Die eine Quelle der Plugin-Version ist src/Version.hpp des Add-ons; die
# Archicad-Hauptversion steht in der Versionsprüfung von cmake/Addon.cmake.
ver () { grep -oE "$2" "$1" | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || echo "?"; }
ARCHICAD=$(ver dcc/archicad/src/Version.hpp 'RTX_ADDON_VERSION "[^"]+"')
ARCHICAD_HOST=$(grep -oE 'ARCHICAD_VERSION STREQUAL "[0-9]+"' dcc/archicad/cmake/Addon.cmake | grep -oE '[0-9]+')
test "$ARCHICAD" != "?" -a -n "$ARCHICAD_HOST" || { echo "Version oder Hostversion nicht lesbar" >&2; exit 1; }
ARCHICAD_ZIP="rendertaxi-archicad${ARCHICAD_HOST}-${ARCHICAD}-${RELEASE}.zip"

ARCHICAD_DIST=dcc/archicad/dist
test -d "$ARCHICAD_DIST/macos/rendertaxi.bundle" || { echo "Fehlt: $ARCHICAD_DIST/macos/rendertaxi.bundle" >&2; exit 1; }
bash scripts/check-archicad-bundle.sh
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
unzip -l "dist/$BLENDER_ZIP" | grep -q ' rendertaxi_client/__init__.py$' || { echo "Blender-Zip ohne eingebetteten rendertaxi_client" >&2; exit 1; }

# Cinema 4D: Version und Hostfassung aus rendertaxi_c4d/host.py (die eine Quelle).
C4D_HOST_PY=dcc/cinema4d/rendertaxi_c4d/host.py
test -f "$C4D_HOST_PY" || { echo "Fehlt: $C4D_HOST_PY" >&2; exit 1; }
test -f dcc/cinema4d/rendertaxi_c4d/rendertaxi_client/__init__.py || { echo "Cinema 4D ohne eingebetteten rendertaxi_client" >&2; exit 1; }
C4D=$(grep -E '^PLUGIN_VERSION = "[0-9]+\.[0-9]+\.[0-9]+"$' "$C4D_HOST_PY" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
C4D_HOST=$(grep -E '^HOST_MAJOR = [0-9]{4}$' "$C4D_HOST_PY" | grep -oE '[0-9]{4}' || true)
test -n "$C4D" -a -n "$C4D_HOST" || { echo "Cinema 4D: Version oder Hostfassung nicht lesbar" >&2; exit 1; }
C4D_ZIP="rendertaxi-cinema4d${C4D_HOST}-${C4D}-${RELEASE}.zip"
C4D_STAGE="$(mktemp -d)"
DIST="$PWD/dist"
cp -R dcc/cinema4d "$C4D_STAGE/rendertaxi"
cp docs/dcc/cinema4d.md "$C4D_STAGE/rendertaxi/ANLEITUNG.md"
(cd "$C4D_STAGE" && zip -qr "$DIST/$C4D_ZIP" rendertaxi -x '*__pycache__*' -x '*.DS_Store' -x '*.pyc')
rm -rf "$C4D_STAGE"
unzip -l "dist/$C4D_ZIP" | grep -q ' rendertaxi/rendertaxi.pyp$' || { echo "Cinema-4D-Zip ohne rendertaxi/rendertaxi.pyp" >&2; exit 1; }

# Rhino: Version aus host.py, das Paket aus dcc/rhino/dist/ (genau eines, passende Version).
RHINO_HOST_PY=dcc/rhino/lib/rendertaxi_rhino/host.py
test -f "$RHINO_HOST_PY" || { echo "Fehlt: $RHINO_HOST_PY" >&2; exit 1; }
test -f dcc/rhino/lib/rendertaxi_rhino/rendertaxi_client/__init__.py || { echo "Rhino ohne eingebetteten rendertaxi_client" >&2; exit 1; }
RHINO=$(grep -E '^PLUGIN_VERSION = "[0-9]+\.[0-9]+\.[0-9]+"$' "$RHINO_HOST_PY" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)
test -n "$RHINO" || { echo "Rhino: Version nicht lesbar" >&2; exit 1; }
RHINO_YAKS=$(ls dcc/rhino/dist/rdtx.ai-"$RHINO"+*-rh8-any.yak 2>/dev/null || true)
test "$(printf '%s\n' "$RHINO_YAKS" | grep -c .)" = 1 || { echo "Rhino: genau ein Paket rdtx.ai-$RHINO+*-rh8-any.yak in dcc/rhino/dist/ erwartet" >&2; exit 1; }
RHINO_YAK=$(basename "$RHINO_YAKS")
cp "$RHINO_YAKS" "dist/$RHINO_YAK"
RHINO_CONTENT=$(unzip -Z1 "dist/$RHINO_YAK")
printf '%s\n' "$RHINO_CONTENT" | grep -qx 'rdtx.ai.rhp' || { echo "Rhino-Paket ohne rdtx.ai.rhp" >&2; exit 1; }

cat > dist/RELEASE.md <<EOF
Release $RELEASE

| Plugin | Hostprogramm | Version | Datei |
| --- | --- | --- | --- |
| Archicad | Archicad $ARCHICAD_HOST (macOS, Windows) | $ARCHICAD | $ARCHICAD_ZIP (macOS-Bundle und Windows-.apx) |
| Blender | Blender $BLENDER_HOST LTS (macOS, Windows, Linux) | $BLENDER | $BLENDER_ZIP (Extension, Vorschau) |
| Cinema 4D | Cinema 4D $C4D_HOST (macOS, Windows) | $C4D | $C4D_ZIP (Python-Plugin, Vorschau) |
| Rhino | Rhino 8 (macOS, Windows) | $RHINO | $RHINO_YAK (yak-Paket, Vorschau) |

**Archicad installieren:** Zip entpacken, Archicad beenden.

- macOS: \`macos/rendertaxi.bundle\` nach \`/Applications/Graphisoft/Archicad 28/Add-Ons/\` kopieren. Das Bundle ist nicht notarisiert; meldet macOS beim Start eine Sperre, einmal \`xattr -dr com.apple.quarantine "/Applications/Graphisoft/Archicad 28/Add-Ons/rendertaxi.bundle"\` im Terminal ausführen.
- Windows: \`win/rendertaxi.apx\` nach \`C:\\Program Files\\Graphisoft\\Archicad 28\\Add-Ons\\\` kopieren. Vorher die Zip freigeben (Rechtsklick › Eigenschaften › Zulassen), sie ist nicht signiert. Die Windows-Fassung ist eine Vorschau, siehe Anleitung.

Danach Archicad starten, Menü **rendertaxi.ai › Palette**. Vollständige Anleitung: [docs/dcc/archicad.md](docs/dcc/archicad.md) (liegt auch als ANLEITUNG.md in der Zip).

**Cinema 4D installieren:** Zip entpacken, Cinema 4D beenden, den Ordner \`rendertaxi\` in den Plugin-Ordner kopieren (**Edit › Preferences › Open Preferences Folder…** › \`plugins\`) und Cinema 4D starten. Danach **Extensions › rendertaxi.ai** (deutsch: **Erweiterungen**). Vollständige Anleitung: [docs/dcc/cinema4d.md](docs/dcc/cinema4d.md) (liegt auch als ANLEITUNG.md im Ordner).

**Rhino installieren:** die Datei \`$RHINO_YAK\` in ein offenes Rhino-8-Fenster ziehen (oder **_PackageManager** › Zahnrad › Datei wählen) und Rhino neu starten. Danach Befehl **RdtxAI** oder die Werkzeugleiste **rdtx.ai**. Vollständige Anleitung: [docs/dcc/rhino.md](docs/dcc/rhino.md).

**Blender installieren:** Zip **nicht** entpacken. In Blender **Edit › Preferences › Get Extensions ›** Menü oben rechts **› Install from Disk…** und die Zip wählen; **Allow Online Access** unter **System › Network** einschalten. Danach in der 3D-Ansicht **N › rendertaxi**. Vollständige Anleitung: [docs/dcc/blender.md](docs/dcc/blender.md).
EOF
ls -l dist
unzip -l "dist/$ARCHICAD_ZIP" | tail -n +1
unzip -l "dist/$BLENDER_ZIP" | tail -n +1
unzip -l "dist/$C4D_ZIP" | tail -n +1
unzip -l "dist/$RHINO_YAK" | tail -n +1
cat dist/RELEASE.md
