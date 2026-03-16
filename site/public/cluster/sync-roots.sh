#!/usr/bin/env bash
set -euo pipefail

PINSET_URL="${PINSET_URL:-https://www2.antoniogarciatrevijano.info/cluster/pins.txt}"
IPFS_BIN="${IPFS_BIN:-ipfs}"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/antoniogarciatrevijano"
STATE_FILE="$STATE_DIR/pins.txt"

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        echo "Missing required command: $1" >&2
        exit 1
    }
}

need_cmd curl
need_cmd "$IPFS_BIN"

mkdir -p "$STATE_DIR"

manifest_file="$(mktemp)"
desired_file="$(mktemp)"
trap 'rm -f "$manifest_file" "$desired_file"' EXIT

curl -fsSL "$PINSET_URL" -o "$manifest_file"

awk 'NF && $1 !~ /^#/' "$manifest_file" | awk '{print $1}' >"$desired_file"

while IFS= read -r line; do
    [ -n "$line" ] || continue
    cid="${line%%[[:space:]]*}"
    label="${line#"$cid"}"
    label="${label#"${label%%[![:space:]]*}"}"

    if "$IPFS_BIN" pin ls --type=recursive "$cid" >/dev/null 2>&1; then
        echo "Already pinned: $cid${label:+ ($label)}"
        continue
    fi

    echo "Pinning: $cid${label:+ ($label)}"
    "$IPFS_BIN" pin add --progress=false "$cid" >/dev/null
done < <(awk 'NF && $1 !~ /^#/' "$manifest_file")

if [ -f "$STATE_FILE" ]; then
    while IFS= read -r old_cid; do
        [ -n "$old_cid" ] || continue
        if grep -qx "$old_cid" "$desired_file"; then
            continue
        fi
        if "$IPFS_BIN" pin ls --type=recursive "$old_cid" >/dev/null 2>&1; then
            echo "Unpinning removed root: $old_cid"
            "$IPFS_BIN" pin rm "$old_cid" >/dev/null || true
        fi
    done <"$STATE_FILE"
fi

cp "$desired_file" "$STATE_FILE"
echo "Pinset synchronized from $PINSET_URL"
