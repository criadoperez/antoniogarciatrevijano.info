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
ARCHIVE_ROOT_CID=$(tr -d '\n' < ipfs/root_cid.txt)

# Add new build
cp -r site/dist /var/www/ipfs/staging/dist
CID=$(docker exec kubo ipfs add -r --cid-version 1 --pin -Q /export/dist)
rm -rf /var/www/ipfs/staging/dist
echo "CID: $CID"

echo "=== Updating MFS ==="
docker exec kubo ipfs files rm -r /www-site 2>/dev/null || true
docker exec kubo ipfs files cp /ipfs/$CID /www-site
echo "MFS: /www-site -> $CID"

echo "=== Updating www2 cluster helpers ==="
install -d /var/www/www-site/cluster
install -m 0644 site/public/cluster/service.json /var/www/www-site/cluster/service.json
install -m 0755 site/public/cluster/sync-roots.sh /var/www/www-site/cluster/sync-roots.sh
cat > /var/www/www-site/cluster/pins.txt <<EOF
# Root CIDs managed by build.sh and served from www2.
# One recursive pin per line: <cid> <label>
${ARCHIVE_ROOT_CID} archive-root
${CID} site-root
EOF
cp /var/www/www-site/cluster/pins.txt site/public/cluster/pins.txt

echo "=== Publishing to IPNS ==="
docker exec kubo ipfs name publish --key=antoniogarciatrevijano "$CID"

echo "=== Pinning to cluster ==="
curl -sf -X POST "http://127.0.0.1:9094/pins/$CID" \
    && echo "Cluster pinned: $CID" || echo "WARNING: cluster pin failed (continuing)"

# Unpin old build from KUBO and cluster, then run GC
if [[ -n "$OLD_CID" && "$OLD_CID" != "$CID" ]]; then
    echo "=== Removing old build ==="
    docker exec kubo ipfs pin rm "$OLD_CID" 2>/dev/null && echo "Unpinned from KUBO: $OLD_CID" || echo "Could not unpin $OLD_CID (may already be unpinned)"
    curl -sf -X DELETE "http://127.0.0.1:9094/pins/$OLD_CID" \
        && echo "Cluster unpinned: $OLD_CID" || echo "WARNING: cluster unpin failed (continuing)"
    docker exec kubo ipfs repo gc --quiet
    echo "GC done"
fi

echo
echo "=== Done ==="
echo "CID: $CID"
echo "www  -> https://www.antoniogarciatrevijano.info  (IPFS via Kubo)"
echo "www2 -> https://www2.antoniogarciatrevijano.info (nginx static)"
