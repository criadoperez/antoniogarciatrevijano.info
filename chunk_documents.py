"""
Step 2: Chunk all DoclingDocument JSONs into text passages for embedding.

Usage:
    python chunk_documents.py

Reads all .json files from output/ (produced by convert_documents.py),
splits each DoclingDocument using Docling's HybridChunker with the bge-m3
tokenizer, and writes all chunks to chunks/chunks.jsonl.

The script is resumable: a progress file tracks which documents have already
been processed. Re-running skips completed documents and appends new chunks.

Each line in chunks/chunks.jsonl is a self-contained chunk:
{
    "text": "...",
    "source_file": "articulos/foo",
    "headings": ["Section title", "Subsection"],
    "page": 3,
    "content_labels": ["paragraph", "paragraph"],
    "origin_filename": "foo.pdf",
    "date": "1996-01-29",
    "publication": "EL MUNDO"
}

date and publication are parsed from the filename convention (YYYY.MMDD.PUBLICATION...)
and omitted for files that don't match.
"""

import json
import re
import sys
import time
from pathlib import Path

from docling_core.transforms.chunker import HybridChunker
from docling_core.transforms.chunker.tokenizer.huggingface import HuggingFaceTokenizer
from docling_core.types.doc import DoclingDocument

# ── Configuration ──────────────────────────────────────────────────────

INPUT_DIR = Path("output")
OUTPUT_DIR = Path("chunks")
OUTPUT_FILE = OUTPUT_DIR / "chunks.jsonl"
PROGRESS_FILE = OUTPUT_DIR / "chunking_progress.json"
EMBEDDING_MODEL = "BAAI/bge-m3"
MAX_TOKENS = 512

# ── Helpers ────────────────────────────────────────────────────────────


def get_page_number(chunk) -> int | None:
    """Extract the first page number referenced by a chunk's doc items."""
    for item in chunk.meta.doc_items:
        for p in getattr(item, "prov", []):
            page = getattr(p, "page_no", None)
            if page is not None:
                return page
    return None


_FILENAME_DATE_RE = re.compile(
    r"^(\d{4})"           # year
    r"[.\-]"              # separator
    r"(\d{4})"            # MMDD
    r"[.\-,]"             # separator (dot, dash, comma — covers typos)
    r"(.+?)$"             # rest (publication.title_author.ext)
)

_FILENAME_DATE_3SEG_RE = re.compile(
    r"^(\d{4})"           # year
    r"[.\-]"              # separator
    r"(\d{2})"            # MM
    r"[.\-]"              # separator
    r"(\d{2})"            # DD
    r"[.\-,]"             # separator
    r"(.+?)$"             # rest
)


def parse_filename_metadata(filename: str) -> dict:
    """
    Extract date and publication from filenames like:
        1996.0129.EL MUNDO.LOS DESIGNIOS DE UN LOCO MORAL_AGT.pdf
        2009.06.21.DIARIORC.REPRESIÓN Y REVOLUCIÓN_AGT.docx
    Returns e.g. {"date": "1996-01-29", "publication": "EL MUNDO"}
    or {} if the filename doesn't match the convention.
    """
    if not filename:
        return {}

    stem = Path(filename).stem

    # Try YYYY.MMDD.REST first (most common)
    m = _FILENAME_DATE_RE.match(stem)
    if m:
        year = m.group(1)
        mmdd = m.group(2)
        rest = m.group(3)
        mm = mmdd[:2]
        dd = mmdd[2:]
    else:
        # Try YYYY.MM.DD.REST
        m = _FILENAME_DATE_3SEG_RE.match(stem)
        if not m:
            return {}
        year = m.group(1)
        mm = m.group(2)
        dd = m.group(3)
        rest = m.group(4)

    # Build ISO date — use 00 for unknown month/day
    if mm == "00":
        date_str = year
    elif dd == "00":
        date_str = f"{year}-{mm}"
    else:
        date_str = f"{year}-{mm}-{dd}"

    # Publication is the first dot-separated segment of rest
    pub = rest.split(".")[0].strip()

    return {"date": date_str, "publication": pub}


def chunk_to_dict(chunk, source_file: str) -> dict:
    """Serialize a chunk to a plain dict suitable for JSONL storage."""
    record = {
        "text": chunk.text,
        "source_file": source_file,
        "headings": chunk.meta.headings or [],
        "page": get_page_number(chunk),
        "content_labels": [item.label.value for item in chunk.meta.doc_items],
    }
    if chunk.meta.origin:
        record["origin_filename"] = chunk.meta.origin.filename
        meta = parse_filename_metadata(chunk.meta.origin.filename)
        if meta:
            record["date"] = meta["date"]
            record["publication"] = meta["publication"]
    return record


def collect_json_files(input_dir: Path) -> list[Path]:
    """Collect all DoclingDocument JSON files, excluding reports."""
    return sorted(
        p for p in input_dir.rglob("*.json")
        if p.name != "conversion_report.json"
    )


def load_progress() -> dict:
    """Load the set of already-processed source files."""
    if PROGRESS_FILE.exists():
        return json.loads(PROGRESS_FILE.read_text(encoding="utf-8"))
    return {}


def save_progress(progress: dict) -> None:
    PROGRESS_FILE.write_text(json.dumps(progress, indent=2), encoding="utf-8")


# ── Main ───────────────────────────────────────────────────────────────


def main():
    if not INPUT_DIR.exists():
        print(f"ERROR: Input directory '{INPUT_DIR}' not found.")
        print("Run convert_documents.py first.")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_files = collect_json_files(INPUT_DIR)
    total_files = len(json_files)
    print(f"Found {total_files} DoclingDocument JSON files in {INPUT_DIR}/\n")

    if total_files == 0:
        print("Nothing to do.")
        return

    # Load progress — skip already-processed documents
    progress = load_progress()
    already_done = set(progress.keys())
    pending = [p for p in json_files if str(p) not in already_done]
    if already_done:
        print(f"Resuming: {len(already_done)} already done, {len(pending)} remaining.\n")

    if not pending:
        total_chunks = sum(v["chunks"] for v in progress.values())
        print(f"All documents already chunked. Total chunks: {total_chunks}")
        return

    print(f"Loading tokenizer: {EMBEDDING_MODEL} (max_tokens={MAX_TOKENS})")
    tokenizer = HuggingFaceTokenizer.from_pretrained(
        model_name=EMBEDDING_MODEL,
        max_tokens=MAX_TOKENS,
    )
    chunker = HybridChunker(
        tokenizer=tokenizer,
        merge_peers=True,
    )
    print("Chunker ready.\n")

    failed = []
    start_time = time.time()
    total_pending = len(pending)

    # Append to existing chunks.jsonl if resuming
    write_mode = "a" if already_done else "w"

    with open(OUTPUT_FILE, write_mode, encoding="utf-8") as out_f:
        for i, json_path in enumerate(pending, 1):
            source_file = str(json_path.relative_to(INPUT_DIR).with_suffix(""))

            try:
                doc = DoclingDocument.load_from_json(json_path)
                chunks = list(chunker.chunk(doc))

                for chunk in chunks:
                    out_f.write(json.dumps(chunk_to_dict(chunk, source_file), ensure_ascii=False) + "\n")
                out_f.flush()

                progress[str(json_path)] = {"chunks": len(chunks), "status": "done"}
                save_progress(progress)
                print(f"[{i}/{total_pending}] {len(chunks):>4} chunks  {json_path.name}")

            except Exception as e:
                failed.append((json_path, str(e)))
                progress[str(json_path)] = {"chunks": 0, "status": "failed", "error": str(e)}
                save_progress(progress)
                print(f"[{i}/{total_pending}] FAIL  {json_path.name} — {e}")

    elapsed = time.time() - start_time
    total_chunks = sum(v["chunks"] for v in progress.values())

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("CHUNKING SUMMARY")
    print("=" * 60)
    print(f"Documents processed:  {total_pending - len(failed)}")
    print(f"Documents failed:     {len(failed)}")
    print(f"Total chunks (all):   {total_chunks}")
    print(f"Avg chunks/doc:       {total_chunks / max(1, len(progress)):.1f}")
    print(f"Time elapsed:         {elapsed:.1f}s ({elapsed / 60:.1f}m)")
    print(f"Output file:          {OUTPUT_FILE}")

    if failed:
        print("\nFailed files:")
        for path, error in failed:
            print(f"  - {path}: {error}")

    report = {
        "embedding_model": EMBEDDING_MODEL,
        "max_tokens": MAX_TOKENS,
        "documents_processed": total_pending - len(failed),
        "documents_failed": len(failed),
        "total_chunks": total_chunks,
        "elapsed_seconds": round(elapsed, 1),
        "failed_files": [{"file": str(p), "error": e} for p, e in failed],
    }
    report_path = OUTPUT_DIR / "chunking_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"Report saved to: {report_path}")


if __name__ == "__main__":
    main()
