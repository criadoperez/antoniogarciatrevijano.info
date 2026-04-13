#!/usr/bin/env bash
set -euo pipefail

# Make python stdout line-buffered so progress prints appear in the log
# in real time (otherwise they sit in an 8KB block buffer and we cannot
# tell whether a long-running step is making progress or stuck).
export PYTHONUNBUFFERED=1

WORK_DIR="/root/antoniogarciatrevijano.info"
LOCK_FILE="/tmp/agt_full_update.lock"
LOG_DIR="$WORK_DIR/logs"

mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/$(date +%Y%m%d_%H%M%S).log"

# Only one instance at a time (flock released automatically on exit/crash)
exec 200>"$LOCK_FILE"
if ! flock -n 200; then
    echo "[$(date)] Skipped — another instance still running." >> "$LOG_DIR/skipped.log"
    exit 0
fi

# Log everything to file; also to terminal if run interactively
if [ -t 1 ]; then
    exec > >(tee -a "$LOG_FILE") 2>&1
else
    exec >> "$LOG_FILE" 2>&1
fi

# Ensure rag-api is restarted even if the script fails mid-pipeline
RAG_STOPPED=false
cleanup() {
    if $RAG_STOPPED; then
        echo ""
        echo "--- Restarting rag-api (cleanup) ---"
        systemctl start rag-api || true
    fi
}
trap cleanup EXIT

echo "=========================================="
echo "AGT Full Update — $(date)"
echo "=========================================="

cd "$WORK_DIR"
source venv/bin/activate

# Clean up logs older than 30 days
find "$LOG_DIR" -name "*.log" -mtime +30 -delete 2>/dev/null || true

echo ""
echo "--- [1/7] convert_documents.py ---"
python3 convert_documents.py

echo ""
echo "--- [2/7] sync_to_ipfs.py ---"
python3 sync_to_ipfs.py

echo ""
echo "--- [3/7] Stopping rag-api for reindexing ---"
systemctl stop rag-api
RAG_STOPPED=true

echo ""
echo "--- [4/7] chunk_documents.py ---"
python3 chunk_documents.py

echo ""
echo "--- [5/7] embed_and_index.py ---"
python3 embed_and_index.py

echo ""
echo "--- [6/7] Starting rag-api ---"
systemctl start rag-api
RAG_STOPPED=false

echo ""
echo "--- [7/7] build.sh + rsync ---"
./build.sh
rsync -a site/dist/ /var/www/www-site/

echo ""
echo "=========================================="
echo "AGT Full Update completed — $(date)"
echo "=========================================="
