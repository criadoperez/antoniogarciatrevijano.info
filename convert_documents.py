"""
Step 1: Convert all documents in ficheros/ to DoclingDocument JSON using Docling.

Usage:
    python convert_documents.py

Pre-step: all .doc files in ficheros/ are converted to .docx in-place using
LibreOffice. The .docx becomes the canonical source file for the rest of the
pipeline (hashing, conversion, chunking, IPFS upload). The original .doc
is left untouched but ignored by all subsequent steps.

Then reads all supported files from ficheros/ (recursively), deduplicates by
content hash (SHA-256), converts unique files using Docling's Standard Pipeline
with GPU-accelerated EasyOCR, and saves:
  - <name>.json  — full DoclingDocument (preserves structure, hierarchy, tables)
  - <name>.md    — human-readable Markdown (for inspection)

Output is written to output/ preserving the subfolder structure.
"""

import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
from docling.datamodel.pipeline_options import (
    EasyOcrOptions,
    PdfPipelineOptions,
    TableFormerMode,
    TableStructureOptions,
)
from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

# ── Configuration ──────────────────────────────────────────────────────

INPUT_DIR = Path("ficheros")
OUTPUT_DIR = Path("output")
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".jpg", ".jpeg", ".png"}
SKIP_EXTENSIONS = {".lnk", ".url", ".doc"}  # .doc handled by pre-step
SKIP_FOLDERS = {"fotos"}  # folders to exclude from RAG pipeline entirely

# ── Setup converter ────────────────────────────────────────────────────


def create_converter() -> DocumentConverter:
    """Create a Docling DocumentConverter, using GPU if available, CPU otherwise."""
    import torch
    device = AcceleratorDevice.CUDA if torch.cuda.is_available() else AcceleratorDevice.CPU
    print(f"Accelerator device: {device.value}")

    ocr_options = EasyOcrOptions(
        lang=["es", "en"],
        force_full_page_ocr=False,
    )

    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        ocr_options=ocr_options,
        do_table_structure=True,
        table_structure_options=TableStructureOptions(
            mode=TableFormerMode.ACCURATE,
            do_cell_matching=True,
        ),
        accelerator_options=AcceleratorOptions(
            device=device,
        ),
    )

    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=pipeline_options),
        }
    )


# ── Helpers ────────────────────────────────────────────────────────────


def convert_all_doc_to_docx(input_dir: Path) -> list[tuple[Path, Path]]:
    """
    Pre-step: find all .doc files and convert them to .docx in the same
    directory using LibreOffice. Returns list of (doc_path, docx_path) for
    files that were converted. Skips if the .docx already exists.
    """
    doc_files = sorted(
        p for p in input_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() == ".doc"
        and not SKIP_FOLDERS.intersection(p.relative_to(input_dir).parts)
    )
    if not doc_files:
        return []

    print(f"Pre-step: converting {len(doc_files)} .doc file(s) to .docx …")
    converted = []
    for i, doc_path in enumerate(doc_files, 1):
        docx_path = doc_path.with_suffix(".docx")
        if docx_path.exists():
            print(f"  [{i}/{len(doc_files)}] SKIP (docx exists): {doc_path.name}")
            continue
        try:
            result = subprocess.run(
                [
                    "libreoffice",
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(doc_path.parent),
                    str(doc_path),
                ],
                capture_output=True,
                timeout=120,
            )
            if not docx_path.exists():
                print(f"  [{i}/{len(doc_files)}] FAIL (no output): {doc_path.name}")
                continue
            converted.append((doc_path, docx_path))
            print(f"  [{i}/{len(doc_files)}] OK: {doc_path.name} → {docx_path.name}")
        except Exception as e:
            print(f"  [{i}/{len(doc_files)}] FAIL: {doc_path.name} — {e}")

    print(f"Pre-step done: {len(converted)} file(s) converted.\n")
    return converted


def collect_files(input_dir: Path) -> list[tuple[Path, str | None]]:
    """
    Collect all processable files, sorted by name.

    Returns list of (path, skip_reason). skip_reason is None for files to
    process, or a string explaining why the file was skipped (e.g. a .pdf
    that has a .docx sibling with the same stem).
    """
    # First pass: collect all candidates
    candidates = []
    for path in sorted(input_dir.rglob("*")):
        if not path.is_file():
            continue
        # Skip files inside excluded folders (e.g. fotos/ — no text to extract)
        if SKIP_FOLDERS.intersection(path.relative_to(input_dir).parts):
            continue
        ext = path.suffix.lower()
        if path.name.endswith(".pdfa.tmp.pdf"):
            continue  # leftover temp file from interrupted PDF/A conversion
        if ext in SUPPORTED_EXTENSIONS:
            candidates.append(path)
        elif ext not in SKIP_EXTENSIONS:
            print(f"  SKIP (unsupported): {path.name}")

    # Build a set of (parent, stem) pairs that have a .docx version
    docx_stems = {
        (p.parent, p.stem.lower())
        for p in candidates
        if p.suffix.lower() == ".docx"
    }

    # Second pass: skip non-.docx files whose stem has a .docx sibling
    files = []
    for path in candidates:
        ext = path.suffix.lower()
        if ext != ".docx" and (path.parent, path.stem.lower()) in docx_stems:
            print(f"  SKIP (docx version exists): {path.name}")
            files.append((path, f"docx version exists: {path.stem}.docx"))
        else:
            files.append((path, None))
    return files


def is_pdfa(pdf_path: Path) -> bool:
    """Check if a PDF is already PDF/A by looking for the PDF/A identifier in metadata."""
    try:
        with open(pdf_path, "rb") as f:
            # Read first 4KB — PDF/A identifier is in the header/metadata
            head = f.read(4096)
            return b"pdfa" in head.lower() or b"pdf/a" in head.lower()
    except Exception:
        return False


_gs_available: bool | None = None


def gs_available() -> bool:
    """Check if Ghostscript is installed. Caches the result."""
    global _gs_available
    if _gs_available is None:
        try:
            subprocess.run(["gs", "--version"], capture_output=True, timeout=5)
            _gs_available = True
        except FileNotFoundError:
            _gs_available = False
    return _gs_available


def convert_pdf_to_pdfa(pdf_path: Path) -> bool:
    """
    Convert a PDF to PDF/A-2b in-place using Ghostscript.
    Returns True if converted, False if failed or gs not installed.
    The original is replaced — Ghostscript reads fully before writing.
    """
    tmp_out = pdf_path.with_suffix(".pdfa.tmp.pdf")
    try:
        result = subprocess.run(
            [
                "gs",
                "-dPDFA=2",
                "-dBATCH",
                "-dNOPAUSE",
                "-dQUIET",
                "-sColorConversionStrategy=RGB",
                "-dOverrideICC=true",
                "-sDEVICE=pdfwrite",
                "-dPDFACompatibilityPolicy=1",  # try to fix non-compliant features
                f"-sOutputFile={tmp_out}",
                str(pdf_path),
            ],
            capture_output=True,
            timeout=600,
        )
        if result.returncode != 0 or not tmp_out.exists():
            if tmp_out.exists():
                tmp_out.unlink()
            return False
        # Replace original with PDF/A version
        tmp_out.replace(pdf_path)
        return True
    except Exception:
        if tmp_out.exists():
            tmp_out.unlink()
        return False


def output_path_for(file_path: Path, input_dir: Path, output_dir: Path) -> Path:
    """Compute output base path (no extension) preserving subfolder structure."""
    relative = file_path.relative_to(input_dir)
    return output_dir / relative.parent / relative.stem


def cleanup_orphaned_output(input_dir: Path, output_dir: Path) -> list[Path]:
    """
    Remove output files (.json, .md) whose source file no longer exists in
    ficheros/. Returns list of removed .json paths.
    """
    removed = []
    for json_path in sorted(output_dir.rglob("*.json")):
        if json_path.name == "conversion_report.json":
            continue
        # output/publicos/articulos/foo.json → ficheros/publicos/articulos/foo
        relative_stem = json_path.relative_to(output_dir).with_suffix("")
        source_base = input_dir / relative_stem
        has_source = any(
            source_base.with_suffix(ext).exists()
            for ext in SUPPORTED_EXTENSIONS | {".doc"}
        )
        if not has_source:
            print(f"  CLEANUP: {json_path.relative_to(output_dir)}")
            json_path.unlink()
            md_path = json_path.with_suffix(".md")
            if md_path.exists():
                md_path.unlink()
            removed.append(json_path)

    # Remove empty directories left behind
    for dir_path in sorted(output_dir.rglob("*"), reverse=True):
        if dir_path.is_dir() and not any(dir_path.iterdir()):
            dir_path.rmdir()

    return removed


def file_hash(path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ── Main ───────────────────────────────────────────────────────────────


def check_prerequisites() -> None:
    """Verify required external tools are installed."""
    if not gs_available():
        print("ERROR: Ghostscript (gs) is not installed.")
        print("Install with: sudo apt install ghostscript")
        sys.exit(1)


def main():
    if not INPUT_DIR.exists():
        print(f"ERROR: Input directory '{INPUT_DIR}' not found.")
        sys.exit(1)

    check_prerequisites()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Pre-step: convert all .doc → .docx in-place
    convert_all_doc_to_docx(INPUT_DIR)

    # Clean up leftover temp files from interrupted PDF/A conversions
    for tmp in INPUT_DIR.rglob("*.pdfa.tmp.pdf"):
        print(f"  Removing leftover temp file: {tmp.name}")
        tmp.unlink()

    # Remove output files whose source was deleted from ficheros/
    orphans = cleanup_orphaned_output(INPUT_DIR, OUTPUT_DIR)
    if orphans:
        print(f"Cleaned up {len(orphans)} orphaned output file(s).\n")

    print(f"Scanning {INPUT_DIR}/ ...")
    all_files = collect_files(INPUT_DIR)
    total = len(all_files)
    skipped_docx_preferred = [(p, reason) for p, reason in all_files if reason]
    files = [p for p, reason in all_files if reason is None]
    print(f"Found {total} files ({len(files)} to process, {len(skipped_docx_preferred)} skipped for docx preference).\n")

    if not files:
        print("Nothing to do.")
        return

    print("Initializing Docling converter (first run downloads models)...")
    converter = create_converter()
    print("Converter ready.\n")

    succeeded = []
    failed = []
    skipped_already = []
    skipped_content_dupes = []  # files with identical content to an earlier file
    seen_hashes: dict[str, Path] = {}  # hash → first file path
    start_time = time.time()
    n_files = len(files)

    for i, file_path in enumerate(files, 1):
        out_path = output_path_for(file_path, INPUT_DIR, OUTPUT_DIR)

        # Skip if output already exists (allows resuming interrupted runs)
        if out_path.with_suffix(".json").exists():
            skipped_already.append(file_path)
            print(f"[{i}/{n_files}] SKIP (already converted): {file_path.name}")
            continue

        # Dedup by content hash
        h = file_hash(file_path)
        first = seen_hashes.get(h)
        if first is not None:
            skipped_content_dupes.append((file_path, first))
            print(f"[{i}/{n_files}] SKIP (duplicate of {first.name}): {file_path.name}")
            continue
        seen_hashes[h] = file_path

        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Convert PDF to PDF/A-2b in-place for long-term archival
        if file_path.suffix.lower() == ".pdf" and not is_pdfa(file_path):
            if convert_pdf_to_pdfa(file_path):
                print(f"[{i}/{n_files}] PDF/A: {file_path.name}")
            else:
                print(f"[{i}/{n_files}] PDF/A FAIL (continuing with original): {file_path.name}")

        # Convert with Docling
        try:
            result = converter.convert(str(file_path))
            # Save full DoclingDocument as JSON (primary format for chunking)
            out_path.with_suffix(".json").write_text(
                result.document.model_dump_json(indent=2), encoding="utf-8"
            )
            # Save Markdown alongside for human inspection
            out_path.with_suffix(".md").write_text(
                result.document.export_to_markdown(), encoding="utf-8"
            )
            succeeded.append(file_path)
            print(f"[{i}/{n_files}] OK: {file_path.name}")
        except Exception as e:
            failed.append((file_path, str(e)))
            print(f"[{i}/{n_files}] FAIL: {file_path.name} — {e}")

    elapsed = time.time() - start_time

    # ── Summary ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("CONVERSION SUMMARY")
    print("=" * 60)
    print(f"Total files found:       {total}")
    print(f"Skipped (docx preferred):{len(skipped_docx_preferred)}")
    print(f"Succeeded:               {len(succeeded)}")
    print(f"Skipped (already done):  {len(skipped_already)}")
    print(f"Skipped (content dupe):  {len(skipped_content_dupes)}")
    print(f"Failed:                  {len(failed)}")
    print(f"Time elapsed:            {elapsed:.1f}s ({elapsed/60:.1f}m)")
    print(f"Output directory:        {OUTPUT_DIR}/")

    if skipped_content_dupes:
        print(f"\nContent duplicates ({len(skipped_content_dupes)}):")
        for dup, original in skipped_content_dupes:
            print(f"  - {dup}  →  duplicate of {original}")

    if failed:
        print(f"\nFailed files:")
        for path, error in failed:
            print(f"  - {path}: {error}")

    # Save report as JSON for later reference
    report = {
        "total": total,
        "succeeded": len(succeeded),
        "skipped_already": len(skipped_already),
        "skipped_docx_preferred": len(skipped_docx_preferred),
        "skipped_content_dupes": len(skipped_content_dupes),
        "failed_count": len(failed),
        "elapsed_seconds": round(elapsed, 1),
        "docx_preferred": [
            {"file": str(p), "reason": reason}
            for p, reason in skipped_docx_preferred
        ],
        "content_dupes": [
            {"file": str(dup), "duplicate_of": str(orig)}
            for dup, orig in skipped_content_dupes
        ],
        "failed_files": [
            {"file": str(p), "error": e} for p, e in failed
        ],
    }
    report_path = OUTPUT_DIR / "conversion_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
