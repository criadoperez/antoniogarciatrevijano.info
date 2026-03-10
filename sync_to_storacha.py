"""
Step 3: Sync ficheros/publicos/ with Storacha (web3.storage / IPFS).

Uploads files not yet in Storacha and removes CIDs for files that no
longer exist locally, keeping Storacha in sync with ficheros/publicos/.

The local file storacha/cids.json is the sync state — it maps each
relative path to its IPFS CID and file hash. Do not delete it; if lost,
the script will re-upload all files (safe: same content = same CID on
IPFS, but creates duplicate upload records in your Storacha space).

Modified files (same path, different content) are detected by SHA-256
hash and re-uploaded automatically.

Usage:
    python sync_to_storacha.py

Prerequisites:
    npm install -g @web3-storage/w3cli
    w3 login your@email.com        # follow the email link
    w3 space create agt-archive    # one-time: create a space
    w3 space use <space-did>       # select it

Run this script after any changes to ficheros/publicos/ (additions,
modifications, or deletions), then re-run embed_and_index.py so Qdrant
payloads include the updated IPFS CIDs.
"""

import hashlib
import json
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────

PUBLIC_DIR = Path("ficheros/publicos")
CIDS_FILE  = Path("storacha/cids.json")
ROOT_CID_FILE = Path("storacha/root_cid.txt")
GATEWAY    = "https://w3s.link/ipfs"
MAX_WORKERS  = 8    # concurrent w3 up processes (>8 triggers Storacha transaction conflicts)
MAX_RETRIES  = 3    # retries per file on transient server errors
RETRY_DELAY  = 4    # initial backoff in seconds (doubles each retry)

# CIDv1 base32 pattern — all Storacha uploads use CIDv1 starting with "baf"
_CID_RE = re.compile(r'\b(baf[a-z2-7]{50,})\b')


# ── w3 CLI wrappers ────────────────────────────────────────────────────

def _w3(*args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["w3", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def check_prerequisites() -> None:
    """Verify w3 CLI is installed and the user is authenticated."""
    try:
        result = _w3("whoami", timeout=15)
    except FileNotFoundError:
        print("ERROR: 'w3' command not found.")
        print("Install with: npm install -g @web3-storage/w3cli")
        sys.exit(1)

    if result.returncode != 0:
        print("ERROR: w3 CLI not authenticated or no space selected.")
        print("  w3 login your@email.com")
        print("  w3 space use <did>")
        sys.exit(1)

    print(f"Authenticated: {result.stdout.strip()}")


def upload_file(abs_path: Path) -> str:
    """
    Upload a single file to Storacha with --no-wrap so the CID refers
    directly to the file (not a wrapping directory).
    Returns the CID string.
    Retries on transient server errors (e.g. DynamoDB TransactionConflict).
    Raises RuntimeError if all attempts fail or the CID cannot be parsed.
    """
    last_error = ""
    for attempt in range(1 + MAX_RETRIES):
        result = _w3("up", "--no-wrap", str(abs_path), timeout=600)
        output = result.stdout + result.stderr

        if result.returncode != 0:
            last_error = output.strip()
            if attempt < MAX_RETRIES:
                delay = RETRY_DELAY * (2 ** attempt)
                time.sleep(delay)
                continue
            raise RuntimeError(f"w3 up failed after {1 + MAX_RETRIES} attempts:\n{last_error}")

        match = _CID_RE.search(output)
        if not match:
            raise RuntimeError(
                f"Upload appeared to succeed but no CID found in output:\n{output.strip()}"
            )

        return match.group(1)


def remove_cid(cid: str) -> None:
    """
    Remove a CID from the Storacha space.
    Non-fatal: if removal fails (e.g. already removed), logs a warning
    and continues so the local mapping is still cleaned up.

    Note: Storacha removes the upload from your space listing immediately.
    Data already committed to Filecoin deals persists until those deals
    expire (typically 18+ months), but it will no longer be served via
    the Storacha gateway.
    """
    result = _w3("rm", cid, timeout=60)
    if result.returncode != 0:
        output = (result.stdout + result.stderr).strip()
        print(f"  WARNING: w3 rm {cid} failed (continuing): {output}")


# ── Helpers ───────────────────────────────────────────────────────────

def file_hash(path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ── State management ───────────────────────────────────────────────────

def load_cids() -> dict[str, dict]:
    """
    Load the path → {cid, hash} mapping from disk.
    Handles the old format (path → cid string) by migrating entries
    to the new format with hash="" (will trigger re-upload on next run).
    """
    if not CIDS_FILE.exists():
        return {}
    raw = json.loads(CIDS_FILE.read_text(encoding="utf-8"))
    result = {}
    for key, value in raw.items():
        if isinstance(value, str):
            # Old format: migrate — empty hash will trigger re-upload
            result[key] = {"cid": value, "hash": ""}
        else:
            result[key] = value
    return result


def save_cids(cids: dict[str, dict]) -> None:
    """Persist the mapping atomically after every upload/removal."""
    CIDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CIDS_FILE.write_text(
        json.dumps(cids, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )


def cid_for_key(cids: dict[str, dict], key: str) -> str:
    """Extract the CID string from a cids entry."""
    entry = cids.get(key, {})
    return entry.get("cid", "") if isinstance(entry, dict) else entry


def collect_local_files() -> dict[str, Path]:
    """
    Scan PUBLIC_DIR recursively.
    Returns {relative_path: absolute_path} e.g.:
      {"articulos/1967.0418.YA.foo.pdf": Path("ficheros/publicos/articulos/...")}.

    Skips .docx files when a .pdf with the same stem exists in the same folder
    (the .pdf is the original source; the .docx is only used for RAG text).
    """
    all_files = [p for p in sorted(PUBLIC_DIR.rglob("*")) if p.is_file()]
    pdf_stems = {
        (p.parent, p.stem.lower())
        for p in all_files
        if p.suffix.lower() == ".pdf"
    }
    result = {}
    for p in all_files:
        if p.suffix.lower() == ".docx" and (p.parent, p.stem.lower()) in pdf_stems:
            continue
        result[str(p.relative_to(PUBLIC_DIR))] = p
    return result


# ── Main ───────────────────────────────────────────────────────────────

def main():
    if not PUBLIC_DIR.exists():
        print(f"ERROR: {PUBLIC_DIR}/ not found.")
        sys.exit(1)

    print("Checking w3 CLI …")
    check_prerequisites()
    print()

    local_files = collect_local_files()
    cids        = load_cids()

    local_set = set(local_files)
    known_set = set(cids)

    to_remove = sorted(known_set - local_set)

    # Determine uploads: new files + modified files (hash changed)
    local_hashes = {}  # rel -> sha256, cached to avoid recomputing after upload
    to_upload = []
    to_reupload = []
    for rel in sorted(local_set):
        if rel not in known_set:
            to_upload.append(rel)
        else:
            h = file_hash(local_files[rel])
            local_hashes[rel] = h
            if h != cids[rel].get("hash", ""):
                to_reupload.append(rel)

    # Pre-compute hashes for new files (avoids hashing after upload)
    for rel in to_upload:
        local_hashes[rel] = file_hash(local_files[rel])

    in_sync = len(local_set) - len(to_upload) - len(to_reupload)

    print(f"Local files:      {len(local_set)}")
    print(f"Already in sync:  {in_sync}")
    print(f"New (to upload):  {len(to_upload)}")
    print(f"Modified (re-up): {len(to_reupload)}")
    print(f"Deleted (remove): {len(to_remove)}")
    print()

    has_changes = to_upload or to_reupload or to_remove

    if not has_changes and ROOT_CID_FILE.exists():
        root_cid = ROOT_CID_FILE.read_text(encoding="utf-8").strip()
        print("Already in sync. Nothing to do.")
        print(f"Root CID: {root_cid}")
        return

    if not has_changes:
        print("Files in sync. Generating root directory CID …\n")

    cids_lock = threading.Lock()  # protects cids dict + save_cids()

    # ── Re-uploads (modified files) ───────────────────────────────────

    if to_reupload:
        print(f"Re-uploading {len(to_reupload)} modified file(s) …\n")

        failed = []
        counter = 0

        def reupload_one(rel):
            new_cid = upload_file(local_files[rel])
            old_cid = cids[rel].get("cid", "")
            if old_cid and old_cid != new_cid:
                remove_cid(old_cid)
            with cids_lock:
                cids[rel] = {"cid": new_cid, "hash": local_hashes[rel]}
                save_cids(cids)
            return new_cid

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(reupload_one, r): r for r in to_reupload}
            for future in as_completed(futures):
                rel = futures[future]
                counter += 1
                size_mb = local_files[rel].stat().st_size / 1_048_576
                try:
                    new_cid = future.result()
                    print(f"[{counter}/{len(to_reupload)}] UPDATED  {rel}  ({size_mb:.1f} MB)")
                    print(f"           CID: {new_cid}")
                except Exception as exc:
                    failed.append((rel, str(exc)))
                    print(f"[{counter}/{len(to_reupload)}] FAIL  {rel} — {exc}")

        if failed:
            print(f"Failed ({len(failed)}):")
            for rel, err in failed:
                print(f"  - {rel}: {err}")
        print()

    # ── New uploads ───────────────────────────────────────────────────

    if to_upload:
        total_size = sum(local_files[r].stat().st_size for r in to_upload)
        print(f"Uploading {len(to_upload)} new file(s)  ({total_size / 1_048_576:.1f} MB total) …\n")

        failed = []
        counter = 0
        start = time.time()

        def upload_one(rel):
            cid = upload_file(local_files[rel])
            with cids_lock:
                cids[rel] = {"cid": cid, "hash": local_hashes[rel]}
                save_cids(cids)
            return cid

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(upload_one, r): r for r in to_upload}
            for future in as_completed(futures):
                rel = futures[future]
                counter += 1
                size_mb = local_files[rel].stat().st_size / 1_048_576
                try:
                    cid = future.result()
                    print(f"[{counter}/{len(to_upload)}] OK  {rel}  ({size_mb:.1f} MB)")
                    print(f"           CID: {cid}")
                except Exception as exc:
                    failed.append((rel, str(exc)))
                    print(f"[{counter}/{len(to_upload)}] FAIL  {rel} — {exc}")

        elapsed = time.time() - start
        print(f"\nUploads complete in {elapsed:.1f}s ({elapsed / 60:.1f}m).")
        if failed:
            print(f"Failed ({len(failed)}):")
            for rel, err in failed:
                print(f"  - {rel}: {err}")
        print()

    # ── Removals ───────────────────────────────────────────────────────

    if to_remove:
        print(f"Removing {len(to_remove)} deleted file(s) from Storacha …\n")

        for i, rel in enumerate(to_remove, 1):
            old_cid = cids.pop(rel).get("cid", "")
            if old_cid:
                remove_cid(old_cid)
            save_cids(cids)  # persist after every removal
            print(f"[{i}/{len(to_remove)}] REMOVED  {rel}")
            if old_cid:
                print(f"           CID: {old_cid}")

        print()

    # ── Directory upload (root CID for pinning) ─────────────────────

    old_root_cid = ""
    if ROOT_CID_FILE.exists():
        old_root_cid = ROOT_CID_FILE.read_text(encoding="utf-8").strip()

    print("Uploading directory to get root CID for pinning …")
    result = _w3("up", str(PUBLIC_DIR), timeout=1800)
    output = result.stdout + result.stderr
    if result.returncode != 0:
        print(f"WARNING: directory upload failed:\n{output.strip()}")
        print("Individual file uploads are fine; root CID was not updated.")
    else:
        match = _CID_RE.search(output)
        if match:
            root_cid = match.group(1)
            ROOT_CID_FILE.write_text(root_cid + "\n", encoding="utf-8")
            print(f"Root CID: {root_cid}")
            print(f"Browse:   {GATEWAY}/{root_cid}")
            print(f"Saved to: {ROOT_CID_FILE}")
            print(f"Pin on another node: ipfs pin add {root_cid}")
            # Remove old root directory CID from Storacha
            if old_root_cid and old_root_cid != root_cid:
                print(f"Removing old root CID: {old_root_cid}")
                remove_cid(old_root_cid)
        else:
            print(f"WARNING: directory upload succeeded but no CID found:\n{output.strip()}")
    print()

    # ── Summary ────────────────────────────────────────────────────────

    print("=" * 60)
    print("SYNC SUMMARY")
    print("=" * 60)
    print(f"Files in Storacha:  {len(cids)}")
    print(f"Gateway:            {GATEWAY}")
    print(f"State file:         {CIDS_FILE}")
    if ROOT_CID_FILE.exists():
        print(f"Root CID:           {ROOT_CID_FILE.read_text(encoding='utf-8').strip()}")
    print()
    print("Next step: re-run embed_and_index.py so Qdrant payloads")
    print("include the updated IPFS CIDs and download URLs.")


if __name__ == "__main__":
    main()
