#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 /path/to/master-controller-source" >&2
    exit 2
fi

SOURCE_ROOT=$1
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DIFF_FILE="$SCRIPT_DIR/changes/0005-ecmaster-foe-firmware-update.diff"

test -f "$SOURCE_ROOT/src/app/amc/amc_oht.cpp"
test -f "$DIFF_FILE"

echo "[1/2] patch dry-run"
patch --dry-run -p1 -d "$SOURCE_ROOT" < "$DIFF_FILE"
echo "[2/2] apply patch"
patch -p1 -d "$SOURCE_ROOT" < "$DIFF_FILE"
echo "Controller patch applied. Rebuild ecmaster and amc_oht with the existing site procedure."
