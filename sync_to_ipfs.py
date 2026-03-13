"""
Step 3: Sync ficheros/publicos/ with the local KUBO IPFS node.

Uploads files not yet pinned on KUBO and unpins CIDs for files that no
longer exist locally, keeping the node in sync with ficheros/publicos/.

The local file ipfs/cids.json is the sync state — it maps each relative
path to its IPFS CID and file hash. Do not delete it; if lost, the script
will re-upload all files (safe: same content = same CID on IPFS, but
re-pins everything from scratch).

Modified files (same path, different content) are detected by SHA-256
hash and re-uploaded automatically.

Usage:
    python sync_to_ipfs.py

Prerequisites:
    KUBO node running and API reachable at http://127.0.0.1:5001
    (docker compose up -d in /var/www/ipfs)

Run this script after any changes to ficheros/publicos/ (additions,
modifications, or deletions), then re-run embed_and_index.py so Qdrant
payloads include the updated IPFS CIDs.
"""

import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# ── Configuration ──────────────────────────────────────────────────────

PUBLIC_DIR    = Path("ficheros/publicos")
AUDIO_DIR     = PUBLIC_DIR / "audios"
CIDS_FILE     = Path("ipfs/cids.json")
ROOT_CID_FILE = Path("ipfs/root_cid.txt")
KUBO_API      = "http://127.0.0.1:5001/api/v0"
GATEWAY       = "https://ipfs.antoniogarciatrevijano.info/ipfs"
MAX_WORKERS   = 4    # concurrent uploads to KUBO
MAX_RETRIES   = 3    # retries per file on transient errors
RETRY_DELAY   = 4    # initial backoff in seconds (doubles each retry)
MFS_ROOT      = "/publicos"  # MFS path used to build the root directory CID


# ── KUBO API wrappers ──────────────────────────────────────────────────

def check_prerequisites() -> None:
    """Verify the KUBO API is reachable."""
    try:
        r = requests.post(f"{KUBO_API}/id", timeout=10)
        r.raise_for_status()
        peer_id = r.json().get("ID", "unknown")
        print(f"KUBO node reachable. Peer ID: {peer_id}")
    except requests.ConnectionError:
        print(f"ERROR: Cannot reach KUBO API at {KUBO_API}")
        print("Make sure the KUBO container is running: docker compose up -d")
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: KUBO API check failed: {exc}")
        sys.exit(1)


def upload_file(abs_path: Path) -> str:
    """
    Add a single file to KUBO with pin=true so the CID refers directly
    to the file (no wrapping directory).
    Returns the CID string.
    Retries on transient errors.
    Raises RuntimeError if all attempts fail or the CID cannot be parsed.
    """
    last_error = ""
    for attempt in range(1 + MAX_RETRIES):
        try:
            with open(abs_path, "rb") as f:
                r = requests.post(
                    f"{KUBO_API}/add",
                    params={"pin": "true", "wrap-with-directory": "false"},
                    files={"file": (abs_path.name, f)},
                    timeout=600,
                )
            if r.status_code != 200:
                last_error = r.text.strip()
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY * (2 ** attempt))
                    continue
                raise RuntimeError(f"KUBO add failed after {1 + MAX_RETRIES} attempts:\n{last_error}")

            # Response is NDJSON; last non-empty line is the added object
            lines = [l for l in r.text.strip().splitlines() if l]
            if not lines:
                raise RuntimeError("KUBO add returned empty response")
            result = json.loads(lines[-1])
            cid = result.get("Hash", "")
            if not cid:
                raise RuntimeError(f"No Hash in KUBO response: {result}")
            return cid

        except (requests.ConnectionError, requests.Timeout) as exc:
            last_error = str(exc)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * (2 ** attempt))
                continue
            raise RuntimeError(f"Upload failed after {1 + MAX_RETRIES} attempts: {last_error}")

    raise RuntimeError(f"Upload failed: {last_error}")


def remove_pin(cid: str) -> None:
    """
    Unpin a CID from the KUBO node.
    Non-fatal: logs a warning if the unpin fails (e.g. already unpinned).
    """
    try:
        r = requests.post(f"{KUBO_API}/pin/rm", params={"arg": cid}, timeout=30)
        if r.status_code != 200:
            print(f"  WARNING: pin/rm {cid} failed (continuing): {r.text.strip()}")
    except Exception as exc:
        print(f"  WARNING: pin/rm {cid} exception (continuing): {exc}")


def build_root_cid(cids: dict[str, dict]) -> str | None:
    """
    Rebuild the MFS directory at MFS_ROOT from the current cids mapping,
    then return the resulting directory CID for pinning on other nodes.
    Returns None if the MFS operations fail.
    """
    print(f"Rebuilding MFS directory {MFS_ROOT} for root CID …")

    # Remove old MFS tree
    requests.post(
        f"{KUBO_API}/files/rm",
        params={"arg": MFS_ROOT, "recursive": "true", "force": "true"},
        timeout=120,
    )

    # Recreate root dir
    r = requests.post(f"{KUBO_API}/files/mkdir",
                      params={"arg": MFS_ROOT, "parents": "true"}, timeout=120)
    if r.status_code != 200:
        print(f"  WARNING: files/mkdir {MFS_ROOT} failed: {r.text.strip()}")
        return None

    # Copy each pinned file into MFS to reconstruct the directory tree
    failed_mfs = 0
    for rel, entry in sorted(cids.items()):
        cid = entry.get("cid", "") if isinstance(entry, dict) else entry
        if not cid:
            continue
        mfs_path = f"{MFS_ROOT}/{rel}"
        parent = str(Path(mfs_path).parent)
        # Ensure parent directory exists
        requests.post(f"{KUBO_API}/files/mkdir",
                      params={"arg": parent, "parents": "true"}, timeout=120)
        r = requests.post(
            f"{KUBO_API}/files/cp",
            params={"arg": [f"/ipfs/{cid}", mfs_path]},
            timeout=120,
        )
        if r.status_code != 200:
            failed_mfs += 1

    if failed_mfs:
        print(f"  WARNING: {failed_mfs} file(s) failed to copy into MFS.")

    # Stat the MFS root to get the directory CID
    r = requests.post(f"{KUBO_API}/files/stat",
                      params={"arg": MFS_ROOT}, timeout=120)
    if r.status_code != 200:
        print(f"  WARNING: files/stat failed: {r.text.strip()}")
        return None

    return r.json().get("Hash")


# ── Helpers ────────────────────────────────────────────────────────────

def file_hash(path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ── State management ───────────────────────────────────────────────────

def load_cids() -> dict[str, dict]:
    """Load the path → {cid, hash} mapping from disk."""
    if not CIDS_FILE.exists():
        return {}
    raw = json.loads(CIDS_FILE.read_text(encoding="utf-8"))
    result = {}
    for key, value in raw.items():
        if isinstance(value, str):
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


def collect_local_files() -> dict[str, Path]:
    """
    Scan PUBLIC_DIR recursively.
    Skips .docx files when a .pdf with the same stem exists in the same folder.
    Returns {relative_path: absolute_path}.
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


# ── Audio CID pre-pass ─────────────────────────────────────────────────

def patch_audio_cids() -> int:
    """
    For every .md in the audios folder whose audio_cid field is empty,
    compute the CID of the paired audio file via KUBO only-hash (no upload)
    and write it into the .md file.

    Must run before collect_local_files() so the patched .md files are
    hashed and uploaded with the correct content.

    Returns the number of .md files patched.
    """
    if not AUDIO_DIR.exists():
        return 0

    patched = 0
    needs_patch = []

    for md_path in sorted(AUDIO_DIR.glob("*.md")):
        content = md_path.read_text(encoding="utf-8")
        if 'audio_cid: ""' not in content:
            continue
        m = re.search(r'^audio_filename:\s*"([^"]+)"', content, re.MULTILINE)
        if not m:
            continue
        audio_path = AUDIO_DIR / m.group(1)
        if not audio_path.exists():
            print(f"  WARNING: audio file not found for {md_path.name}, skipping")
            continue
        needs_patch.append((md_path, audio_path, content))

    if not needs_patch:
        return 0

    print(f"  Computing CIDs for {len(needs_patch)} audio file(s) …")
    for md_path, audio_path, content in needs_patch:
        try:
            with open(audio_path, "rb") as f:
                r = requests.post(
                    f"{KUBO_API}/add",
                    params={"only-hash": "true", "wrap-with-directory": "false"},
                    files={"file": (audio_path.name, f)},
                    timeout=300,
                )
            r.raise_for_status()
            lines = [ln for ln in r.text.strip().splitlines() if ln]
            cid = json.loads(lines[-1]).get("Hash", "")
            if not cid:
                print(f"  WARNING: no CID returned for {audio_path.name}, skipping")
                continue
        except Exception as exc:
            print(f"  WARNING: only-hash failed for {audio_path.name}: {exc}")
            continue

        new_content = content.replace('audio_cid: ""', f'audio_cid: "{cid}"', 1)
        md_path.write_text(new_content, encoding="utf-8")
        print(f"  Patched {md_path.name}  →  {cid}")
        patched += 1

    return patched


# ── Main ───────────────────────────────────────────────────────────────

def main():
    if not PUBLIC_DIR.exists():
        print(f"ERROR: {PUBLIC_DIR}/ not found.")
        sys.exit(1)

    print("Checking KUBO API …")
    check_prerequisites()
    print()

    print("Patching audio_cid fields in .md files …")
    patched = patch_audio_cids()
    if patched:
        print(f"  Patched {patched} .md file(s).\n")
    else:
        print("  Nothing to patch.\n")

    local_files = collect_local_files()
    cids        = load_cids()

    local_set = set(local_files)
    known_set = set(cids)

    to_remove = sorted(known_set - local_set)

    # Determine uploads: new files + modified files (hash changed)
    local_hashes: dict[str, str] = {}
    to_upload   : list[str] = []
    to_reupload : list[str] = []
    for rel in sorted(local_set):
        if rel not in known_set:
            to_upload.append(rel)
        else:
            h = file_hash(local_files[rel])
            local_hashes[rel] = h
            if h != cids[rel].get("hash", ""):
                to_reupload.append(rel)

    for rel in to_upload:
        local_hashes[rel] = file_hash(local_files[rel])

    in_sync = len(local_set) - len(to_upload) - len(to_reupload)

    print(f"Local files:      {len(local_set)}")
    print(f"Already in sync:  {in_sync}")
    print(f"New (to upload):  {len(to_upload)}")
    print(f"Modified (re-up): {len(to_reupload)}")
    print(f"Deleted (unpin):  {len(to_remove)}")
    print()

    has_changes = to_upload or to_reupload or to_remove

    if not has_changes and ROOT_CID_FILE.exists():
        root_cid = ROOT_CID_FILE.read_text(encoding="utf-8").strip()
        print("Already in sync. Nothing to do.")
        print(f"Root CID: {root_cid}")
        return

    cids_lock = threading.Lock()

    # ── Re-uploads (modified files) ────────────────────────────────────

    if to_reupload:
        print(f"Re-uploading {len(to_reupload)} modified file(s) …\n")
        failed = []
        counter = 0

        def reupload_one(rel):
            new_cid = upload_file(local_files[rel])
            old_cid = cids[rel].get("cid", "")
            if old_cid and old_cid != new_cid:
                remove_pin(old_cid)
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
            print(f"\nFailed ({len(failed)}):")
            for rel, err in failed:
                print(f"  - {rel}: {err}")
        print()

    # ── New uploads ────────────────────────────────────────────────────

    if to_upload:
        total_size = sum(local_files[r].stat().st_size for r in to_upload)
        print(f"Uploading {len(to_upload)} new file(s)  ({total_size / 1_048_576:.1f} MB total) …\n")

        failed  = []
        counter = 0
        start   = time.time()

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
        print(f"Unpinning {len(to_remove)} deleted file(s) from KUBO …\n")
        for i, rel in enumerate(to_remove, 1):
            entry = cids.pop(rel)
            old_cid = entry.get("cid", "") if isinstance(entry, dict) else entry
            if old_cid:
                remove_pin(old_cid)
            save_cids(cids)
            print(f"[{i}/{len(to_remove)}] UNPINNED  {rel}")
            if old_cid:
                print(f"           CID: {old_cid}")
        print()

    # ── Root directory CID (for pinning on other nodes) ────────────────

    root_cid = build_root_cid(cids)
    if root_cid:
        ROOT_CID_FILE.parent.mkdir(parents=True, exist_ok=True)
        ROOT_CID_FILE.write_text(root_cid + "\n", encoding="utf-8")
        print(f"Root CID:  {root_cid}")
        print(f"Browse:    {GATEWAY}/{root_cid}")
        print(f"Saved to:  {ROOT_CID_FILE}")
        print(f"Pin elsewhere: ipfs pin add {root_cid}")
    print()

    # ── Summary ────────────────────────────────────────────────────────

    print("=" * 60)
    print("SYNC SUMMARY")
    print("=" * 60)
    print(f"Files pinned on KUBO:  {len(cids)}")
    print(f"Gateway:               {GATEWAY}")
    print(f"State file:            {CIDS_FILE}")
    if ROOT_CID_FILE.exists():
        print(f"Root CID:              {ROOT_CID_FILE.read_text(encoding='utf-8').strip()}")
    print()
    print("Next step: re-run embed_and_index.py so Qdrant payloads")
    print("include the updated IPFS CIDs and download URLs.")


if __name__ == "__main__":
    main()
