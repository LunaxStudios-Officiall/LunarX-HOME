#!/bin/sh
set -eu

# Inspectable bootstrap: download into a temporary directory, optionally
# verify a caller-provided digest, then hand off to the local installer. It
# never pipes a remote script to root and never writes outside the temp tree
# until the staged installer is invoked.
operation=${1:-install}
case "$operation" in
    install|update|upgrade) ;;
    *) echo "Usage: bootstrap.sh {install|update|upgrade}" >&2; exit 2 ;;
esac

command -v curl >/dev/null 2>&1 || { echo "curl is required" >&2; exit 2; }
command -v unzip >/dev/null 2>&1 || { echo "unzip is required" >&2; exit 2; }
tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/lunarx-bootstrap.XXXXXX")
trap 'rm -rf "$tmp_dir"' EXIT HUP INT TERM
archive="$tmp_dir/lunarx-home.zip"
url=${LUNARX_SOURCE_URL:-https://github.com/LunaxStudios-Officiall/LunarX-HOME/archive/refs/heads/main.zip}

curl --fail --location --proto '=https' --tlsv1.2 --silent --show-error "$url" -o "$archive"
if [ -n "${LUNARX_BOOTSTRAP_SHA256:-}" ]; then
    printf '%s  %s\n' "$LUNARX_BOOTSTRAP_SHA256" "$archive" | sha256sum --check --status -
fi
unzip -q "$archive" -d "$tmp_dir/source"
source_dir=$(find "$tmp_dir/source" -mindepth 1 -maxdepth 1 -type d | head -n 1)
[ -n "$source_dir" ] || { echo "downloaded source did not contain a project directory" >&2; exit 1; }
exec "$source_dir/install.sh" "$operation"
