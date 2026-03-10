#!/usr/bin/env bash
# Full rebuild: catalog generation + Astro static site build.
# Run from the project root (agt/).
#
# Usage:
#   ./build.sh           # full rebuild
#   ./build.sh --site    # skip catalog, rebuild site only
set -euo pipefail

cd "$(dirname "$0")"

if [[ "${1:-}" != "--site" ]]; then
    echo "=== Generating catalog ==="
    python3 build_catalog.py
    echo
fi

echo "=== Building site ==="
cd site
npm run build

# Copy pagefind assets to public/ so search works in dev mode too
if [ -d dist/pagefind ]; then
    echo "=== Syncing pagefind to public/ ==="
    rm -rf public/pagefind
    cp -r dist/pagefind public/pagefind
else
    echo "WARNING: dist/pagefind not found — search won't work in dev mode"
fi

echo
echo "=== Done ==="
echo "Output: site/dist/"
echo "Preview: cd site && npm run preview"
