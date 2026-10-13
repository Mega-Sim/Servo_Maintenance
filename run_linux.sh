#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CONFIG=${1:-$SCRIPT_DIR/linux/servo_maintenance.ini}
BRIDGE_PID=

cleanup()
{
    if [ -n "$BRIDGE_PID" ]; then
        kill "$BRIDGE_PID" 2>/dev/null || true
        wait "$BRIDGE_PID" 2>/dev/null || true
    fi
}

trap cleanup EXIT INT TERM
cd "$SCRIPT_DIR"
python3 linux/servo_maintenance_tcp_bridge.py --config "$CONFIG" &
BRIDGE_PID=$!
python3 linux/servo_maintenance_web.py --config "$CONFIG"
