"""
Step 4+5: Embed all chunks and store them in a local Qdrant vector database.

Usage:
    python embed_and_index.py

Reads chunks/chunks.jsonl (produced by chunk_documents.py), embeds each chunk
using BAAI/bge-m3 (dense vectors, 1024 dimensions) on GPU, and upserts them
into a local Qdrant collection stored at qdrant_db/.

IPFS support:
    If storacha/cids.json exists (produced by sync_to_storacha.py), each chunk
    from a public file gets its IPFS CID and a permanent download_url stored in
    the Qdrant payload. Run sync_to_storacha.py before this script.
    If cids.json is absent, indexing still works — chunks just won't have
    download URLs until you run the sync and re-index.

Resumable: on restart, counts existing points in the collection and skips
that many lines in the JSONL, continuing from where it left off.
"""

import json
import sys
import time
from pathlib import Path

# Compatibility shim: FlagEmbedding's reranker imports is_torch_fx_available,
# which was removed in transformers>=4.48. We only need the embedder, not the
# reranker, so patch the missing symbol before importing.
import transformers.utils.import_utils as _tfi
if not hasattr(_tfi, "is_torch_fx_available"):
    _tfi.is_torch_fx_available = lambda: False

from FlagEmbedding import BGEM3FlagModel
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

# ── Configuration ──────────────────────────────────────────────────────

CHUNKS_FILE     = Path("chunks/chunks.jsonl")
CIDS_FILE       = Path("storacha/cids.json")
QDRANT_PATH     = Path("qdrant_db")
COLLECTION_NAME = "documents"
EMBEDDING_MODEL = "BAAI/bge-m3"
IPFS_GATEWAY    = "https://w3s.link/ipfs"
VECTOR_SIZE     = 1024   # bge-m3 dense output dimension
BATCH_SIZE      = 256    # tuned for 64GB RAM + RTX 3070 Ti 8GB VRAM
MAX_LENGTH      = 512    # max tokens per chunk fed to the model


# ── IPFS helpers ───────────────────────────────────────────────────────

def load_cids() -> dict[str, str]:
    """
    Load the relative-path → CID mapping produced by sync_to_storacha.py.
    Keys are paths relative to ficheros/publicos/, e.g. "articulos/foo.pdf".
    Returns a flat {path: cid} dict. Handles both old format (path → cid string)
    and new format (path → {cid, hash}).
    """
    if CIDS_FILE.exists():
        raw = json.loads(CIDS_FILE.read_text(encoding="utf-8"))
        data = {}
        for key, value in raw.items():
            if isinstance(value, dict):
                data[key] = value.get("cid", "")
            else:
                data[key] = value
        print(f"Loaded {len(data)} IPFS CIDs from {CIDS_FILE}")
        return data

    print(f"NOTE: {CIDS_FILE} not found — download_url will be null for all chunks.")
    print("      Run sync_to_storacha.py first, then re-run this script.")
    return {}


def _cid_lookup_key(source_file: str, origin_filename: str) -> str | None:
    """
    Compute the cids.json key for a chunk's source file, or None if the file
    is not public (i.e. not under publicos/).

    source_file     "publicos/articulos/foo"   (relative to output/, no extension)
    origin_filename "foo.pdf"                  (original filename with extension)
    → key           "articulos/foo.pdf"        (relative to ficheros/publicos/)
    """
    if not source_file.startswith("publicos/"):
        return None
    rel = source_file[len("publicos/"):]               # "articulos/foo"
    if origin_filename:
        return str(Path(rel).parent / origin_filename)  # "articulos/foo.pdf"
    return rel


def get_cid_and_url(
    source_file: str, origin_filename: str, cids: dict[str, str]
) -> tuple[str, str | None]:
    """
    Return (cid, download_url) for a chunk.
    cid is "" and download_url is None for private files or unmapped public files.

    When a chunk comes from a .docx but the original .pdf exists in Storacha,
    the PDF's CID is returned (the .docx was used for RAG text quality, but the
    .pdf is the original source document).
    """
    key = _cid_lookup_key(source_file, origin_filename)
    if key is None:
        return "", None
    cid = cids.get(key, "")
    # Prefer the .pdf original when the chunk came from a .docx transcription
    if not cid and key.lower().endswith(".docx"):
        pdf_key = key[:-5] + ".pdf"
        cid = cids.get(pdf_key, "")
    url = f"{IPFS_GATEWAY}/{cid}" if cid else None
    return cid, url


# ── Qdrant helpers ─────────────────────────────────────────────────────

def load_model() -> BGEM3FlagModel:
    import torch
    cuda_available = torch.cuda.is_available()
    device = "cuda:0" if cuda_available else "cpu"
    print(f"Loading embedding model: {EMBEDDING_MODEL} (device: {device})")
    model = BGEM3FlagModel(
        EMBEDDING_MODEL,
        use_fp16=cuda_available,  # fp16 only beneficial on CUDA
        devices=[device],
    )
    print("Model loaded.\n")
    return model


def setup_collection(client: QdrantClient) -> int:
    """Create collection if needed. Returns count of already-indexed points."""
    if client.collection_exists(COLLECTION_NAME):
        count = client.count(COLLECTION_NAME).count
        print(f"Collection '{COLLECTION_NAME}' exists with {count} points.")
        return count

    client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
    )
    print(f"Created collection '{COLLECTION_NAME}'.")
    return 0


# ── Main ───────────────────────────────────────────────────────────────

def main():
    if not CHUNKS_FILE.exists():
        print(f"ERROR: '{CHUNKS_FILE}' not found.")
        print("Run chunk_documents.py first.")
        sys.exit(1)

    # Load IPFS CIDs (optional — indexing proceeds without them)
    cids = load_cids()
    print()

    # Count total chunks for progress display
    print(f"Counting chunks in {CHUNKS_FILE} …")
    with open(CHUNKS_FILE, encoding="utf-8") as f:
        total_chunks = sum(1 for _ in f)
    print(f"Total chunks: {total_chunks}\n")

    # Init Qdrant
    QDRANT_PATH.mkdir(parents=True, exist_ok=True)
    client = QdrantClient(path=str(QDRANT_PATH))
    already_indexed = setup_collection(client)

    if already_indexed > total_chunks:
        print(
            f"ERROR: Qdrant has {already_indexed} points but chunks.jsonl only has "
            f"{total_chunks} lines. The JSONL may have been regenerated after indexing.\n"
            f"To reindex from scratch, delete the '{QDRANT_PATH}' directory and re-run."
        )
        sys.exit(1)

    if already_indexed == total_chunks:
        print("All chunks already indexed. Nothing to do.")
        return

    if already_indexed > 0:
        print(f"Resuming from chunk {already_indexed} / {total_chunks}.\n")
    else:
        print()

    # Load model only after confirming there's work to do
    model = load_model()

    start_time     = time.time()
    batch_texts    = []
    batch_payloads = []
    point_id       = already_indexed

    def flush_batch():
        """Embed and upsert the current batch."""
        nonlocal point_id
        output = model.encode(
            batch_texts,
            batch_size=BATCH_SIZE,
            max_length=MAX_LENGTH,
            return_dense=True,
            return_sparse=False,
            return_colbert_vecs=False,
        )
        vectors = output["dense_vecs"]
        points = [
            PointStruct(
                id=point_id + j,
                vector=vectors[j].tolist(),
                payload=batch_payloads[j],
            )
            for j in range(len(batch_texts))
        ]
        client.upsert(collection_name=COLLECTION_NAME, wait=True, points=points)
        point_id += len(batch_texts)

    with open(CHUNKS_FILE, encoding="utf-8") as f:
        # Skip already-indexed lines
        for _ in range(already_indexed):
            f.readline()

        for line in f:
            chunk = json.loads(line)
            sf = chunk.get("source_file", "")
            of = chunk.get("origin_filename", "")
            cid, download_url = get_cid_and_url(sf, of, cids)

            batch_texts.append(chunk["text"])
            batch_payloads.append({
                "text":            chunk["text"],
                "source_file":     sf,
                "origin_filename": of,
                "headings":        chunk.get("headings", []),
                "page":            chunk.get("page"),
                "content_labels":  chunk.get("content_labels", []),
                "date":            chunk.get("date", ""),
                "publication":     chunk.get("publication", ""),
                "cid":             cid,           # IPFS CID — "" if not public/not synced
                "download_url":    download_url,  # permanent IPFS URL or null
            })

            if len(batch_texts) >= BATCH_SIZE:
                flush_batch()
                elapsed   = time.time() - start_time
                rate      = (point_id - already_indexed) / elapsed
                remaining = (total_chunks - point_id) / rate if rate > 0 else 0
                print(
                    f"[{point_id}/{total_chunks}]  "
                    f"{rate:.1f} chunks/s  "
                    f"ETA: {remaining/60:.1f}m"
                )
                batch_texts.clear()
                batch_payloads.clear()

    # Flush remaining partial batch
    if batch_texts:
        flush_batch()

    elapsed = time.time() - start_time

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("INDEXING SUMMARY")
    print("=" * 60)
    final_count = client.count(COLLECTION_NAME).count
    print(f"Chunks indexed this run: {point_id - already_indexed}")
    print(f"Total points in DB:      {final_count}")
    print(f"IPFS CIDs loaded:        {len(cids)}")
    print(f"Collection:              {COLLECTION_NAME}")
    print(f"Database path:           {QDRANT_PATH}/")
    print(f"Time elapsed:            {elapsed:.1f}s ({elapsed/60:.1f}m)")


if __name__ == "__main__":
    main()
