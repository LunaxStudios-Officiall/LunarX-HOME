#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALLER="$SCRIPT_DIR/installer/lunarx_installer.py"

if [ ! -f "$INSTALLER" ]; then
    echo "LunarX installer is incomplete: $INSTALLER was not found." >&2
    exit 2
fi

if [ "$#" -lt 1 ]; then
    echo "Usage: ./install.sh {install|update|upgrade|repair|doctor|status|uninstall} [options]" >&2
    exit 2
fi

exec python3 "$INSTALLER" "$@" --source "$SCRIPT_DIR"
