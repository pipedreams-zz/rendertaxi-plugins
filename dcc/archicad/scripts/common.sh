# Gemeinsame Einstellungen der drei Skripte. Wird eingebunden, nicht ausgeführt.
set -euo pipefail

ADDON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Gebaut wird **außerhalb** des Repositorys. Das ist nicht nur Ordnung: der
# Makefile-Generator von CMake legt Dateien namens `compiler_depend.ts` an, und
# `pnpm lint` des Monorepos greift jede `.ts` im Baum auf. Ein Buildverzeichnis
# im Repository ließe damit die CI an einer erzeugten Datei scheitern.
BUILD_ROOT="${RTX_BUILD_ROOT:-${HOME}/Library/Caches/rendertaxi/archicad-addon}"
BUILD_DIR="${RTX_BUILD_DIR:-${BUILD_ROOT}/build}"
CONFIG="${RTX_CONFIG:-Release}"
ADDON_NAME="rendertaxi"

# Das DevKit gehört nicht ins Repository (Festlegung 7 aus Issue #20). Sein Pfad
# kommt aus der Umgebung; dieser Vorgabewert ist die Ablage auf dem MacBook,
# an dem dieser Auftrag entstanden ist.
DEVKIT_DEFAULT="${HOME}/Documents/coding/rtx.ai/sdk/API.Development.Kit.MAC.28.4001"
DEVKIT="${AC_API_DEVKIT_DIR:-${DEVKIT_DEFAULT}}"

# Archicad 28 auf macOS; der Add-On-Ordner liegt neben der Anwendung.
ARCHICAD_DIR="${RTX_ARCHICAD_DIR:-/Applications/Graphisoft/Archicad 28}"

say () { printf '%s\n' "$*"; }
die () { printf 'Fehler: %s\n' "$*" >&2; exit 1; }
