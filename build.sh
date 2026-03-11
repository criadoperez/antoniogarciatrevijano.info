#!/usr/bin/env bash
# Full rebuild: catalog generation + Astro static site build + IPFS publish.
# Run from the project root.
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

cd ..

echo
echo "=== Publishing to IPFS ==="

# Resolve current IPNS to get the old CID (so we can unpin it after)
OLD_CID=$(docker exec kubo ipfs name resolve /ipns/k51qzi5uqu5dm9uonrvozk33zarhrb5mql3mzyfy3rzecedoucnhkx4gh8lbf1 2>/dev/null | sed 's|/ipfs/||' || echo "")

# Add new build
cp -r site/dist /var/www/ipfs/staging/dist
CID=$(docker exec kubo ipfs add -r --cid-version 1 --pin -Q /export/dist)
rm -rf /var/www/ipfs/staging/dist
echo "CID: $CID"

echo "=== Updating MFS ==="
docker exec kubo ipfs files rm -r /www-site 2>/dev/null || true
docker exec kubo ipfs files cp /ipfs/$CID /www-site
echo "MFS: /www-site -> $CID"

echo "=== Publishing to IPNS ==="
docker exec kubo ipfs name publish --key=antoniogarciatrevijano "$CID"

# Unpin old build and run GC
if [[ -n "$OLD_CID" && "$OLD_CID" != "$CID" ]]; then
    echo "=== Removing old build ==="
    docker exec kubo ipfs pin rm "$OLD_CID" 2>/dev/null && echo "Unpinned: $OLD_CID" || echo "Could not unpin $OLD_CID (may already be unpinned)"
    docker exec kubo ipfs repo gc --quiet
    echo "GC done"
fi

echo
echo "=== Done ==="
echo "CID: $CID"
echo "www  -> https://www.antoniogarciatrevijano.info  (IPFS via Kubo)"
echo "www2 -> https://www2.antoniogarciatrevijano.info (nginx static)"
