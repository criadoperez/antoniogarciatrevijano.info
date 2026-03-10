# RAG Pipeline with Docling

Guide to building a Retrieval-Augmented Generation (RAG) system from a folder of documents (PDFs, images, DOCX) using Docling for document conversion.

---

## Current Status

| Step | Script | Status | Notes |
|------|--------|--------|-------|
| 1 -- Document Conversion | `convert_documents.py` | Needs re-run | Dedup logic added; re-run with clean `output/` |
| 2 -- Chunking | `chunk_documents.py` | Needs re-run | Re-run with clean `chunks/` to pick up `date`/`publication` fields |
| 3 -- IPFS Sync | `sync_to_storacha.py` | Not started | Needs Storacha account + w3 CLI setup |
| 4+5 -- Embedding + Indexing | `embed_and_index.py` | Needs re-run | Re-run after step 2 with clean `qdrant_db/` to store `date`/`publication` |
| 6 -- Query Interface | `rag_api.py` | Running | Single FastAPI service: RAG + LLM + streaming + OpenAI-compatible API |

### Content deduplication

Three dedup mechanisms prevent duplicate content from entering the pipeline:

1. **`.doc` → `.docx` pre-step** (`convert_documents.py`): all `.doc` files are converted to `.docx` in-place using LibreOffice before anything else. The `.docx` becomes the canonical source file; `.doc` is ignored by all subsequent steps.

2. **`.docx` preferred over `.pdf`** (`convert_documents.py`, `sync_to_storacha.py`): when the same filename stem exists as both `.docx` and `.pdf` in the same folder, the `.docx` is used for RAG (cleaner text from handmade transcription) and the `.pdf` is uploaded to Storacha (original source). `embed_and_index.py` links chunks from the `.docx` to the `.pdf`'s download URL.

3. **Content hash dedup** (`convert_documents.py`): files are hashed (SHA-256) before Docling conversion. If two files anywhere in `ficheros/` have identical content (different names, paths, or extensions), only the first is converted. Duplicates are logged and listed in `conversion_report.json`.

### PDF/A-2b conversion

Before Docling processes a PDF, `convert_documents.py` converts it to PDF/A-2b in-place using Ghostscript (if not already PDF/A). This modernizes the container format for long-term archival without affecting content or OCR accuracy — Docling still runs its own EasyOCR on scanned pages. Ghostscript (`gs`) is required — the script checks for it at startup and exits with an install instruction if missing.

---

### ~~qdrant-client `.search()` removed in 1.16.x~~ -- FIXED

`qdrant-client` 1.16.2 removed the `.search()` method. Use `.query_points()` instead:

```python
result = client.query_points(
    collection_name=COLLECTION,
    query=vector,          # was query_vector=
    limit=top_k,
    with_payload=True,
    score_threshold=MIN_SCORE,
)
for hit in result.points:  # was: for hit in hits:
```

Also, newer OpenAI models (gpt-5.4+) require `max_completion_tokens` instead of `max_tokens`.

---

### ~~Blocker: qdrant-client import error~~ -- FIXED

`qdrant-client==1.17.0` introduced a bug in `grpc_uploader.py` line 24: `grpc.UpdateMode | None` as a runtime annotation. `grpc.UpdateMode` is a protobuf `EnumTypeWrapper` (not a real Python type), so it doesn't support the `|` operator -- crashing at import time.

The bug exists **only in 1.17.0**. All versions 1.13.x-1.16.2 are clean.

**Fix applied:** downgraded to `qdrant-client==1.16.2`.

---

## Architecture

Single machine running everything: document processing (steps 1-5), vector database, RAG API, and LLM calls.

```
Static site (IPFS)            Server (agt.criadoperez.com)
+----------------+            +------------------------------+
|  HTML/JS       |--POST /chat-->  RAG API (rag_api.py)      |
|  chat widget   |<--stream---|    |- bge-m3 (embedding)     |
+----------------+            |    |- Qdrant (vector DB)     |
                              |    '- OpenAI API (LLM)       |
OpenWebUI (LAN)               |                              |
+----------------+            |                              |
|  Chat UI       |--/v1/chat/completions-->                  |
|                |<--stream---|                              |
+----------------+            +------------------------------+
```

The frontend is a static HTML/JS page hosted on IPFS. It sends the user's question to the RAG API via `POST /chat`. The server does retrieval, calls the OpenAI API with its own key (never exposed to the browser), and streams the answer back via SSE.

OpenWebUI (or any OpenAI-compatible client) can connect via the `/v1/chat/completions` endpoint.

---

## Project File Structure

```
agt/
|-- ficheros/                        # Source documents (input)
|   |-- publicos/                    # Public files -- indexed + hosted on IPFS via Storacha
|   |   |-- articulos/               # Press articles (~2,500 files)
|   |   '-- AGT.LIBROS/              # Books (~40 files)
|   '-- privados/                    # Private files -- indexed for RAG only, never served
|
|-- output/                          # Step 1 output -- pipeline intermediate (not served)
|   |-- publicos/
|   |   |-- articulos/
|   |   |   |-- <doc>.json           # DoclingDocument (used for chunking)
|   |   |   '-- <doc>.md             # Markdown (human inspection only)
|   |   '-- AGT.LIBROS/
|   |-- privados/
|   '-- conversion_report.json
|
|-- chunks/                          # Step 2 output -- pipeline intermediate (not served)
|   |-- chunks.jsonl                 # All chunks -- one JSON object per line
|   |-- chunking_progress.json       # Resume tracker
|   '-- chunking_report.json
|
|-- storacha/                        # Step 3 output
|   |-- cids.json                    # Maps relative path -> IPFS CID for every public file
|   |                                # DO NOT DELETE -- this is the sync state
|   '-- root_cid.txt                 # Root directory CID (for pinning on other IPFS nodes)
|
|-- qdrant_db/                       # Step 4+5 output -- Qdrant embedded DB
|
|-- venv/                            # Python virtual environment
|-- requirements.txt                 # Python dependencies
|-- .env                             # OPENAI_API_KEY, LLM_MODEL, RAG_API_KEY
|-- convert_documents.py             # Step 1
|-- chunk_documents.py               # Step 2
|-- sync_to_storacha.py              # Step 3 -- IPFS sync
|-- embed_and_index.py               # Step 4+5
|-- rag_api.py                       # Step 6 -- FastAPI service (RAG + LLM + streaming)
'-- README.md                        # This file
```

---

## Environment

- **Docling version:** 2.76.0
- **Python:** 3.13 (venv at `venv/`)
- **OS:** Linux

All commands must run inside the virtual environment. Either activate it first or use the venv Python directly:

```bash
source venv/bin/activate   # then use python / pip as normal
# or
venv/bin/python <script>   # without activating
```

The server needs enough RAM to load bge-m3 (~2.3 GB) plus Qdrant. A GPU is beneficial for steps 1 and 4+5 (document conversion and embedding) but not required -- the RAG API runs on CPU.

## Dependencies

### Python packages

```bash
python -m venv venv
venv/bin/python -m ensurepip          # needed on some distros (Debian/Ubuntu)
venv/bin/pip install -r requirements.txt
```

For GPU support, install PyTorch with the correct CUDA version **before** the requirements file:

```bash
venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu121  # adjust cu121 to your CUDA version
venv/bin/pip install -r requirements.txt
```

| Package | Version | Purpose |
|---------|---------|---------|
| `docling` | 2.76.0 | Document conversion engine (PDF, DOCX, images -> DoclingDocument JSON) |
| `easyocr` | 1.7.2 | OCR engine for scanned PDFs and images. Supports 80+ languages and GPU acceleration. |
| `FlagEmbedding` | 1.3.5 | Official BAAI library for bge-m3. Provides `BGEM3FlagModel` with native batched GPU inference. |
| `qdrant-client` | 1.16.2 | Python client for Qdrant vector database. Used in embedded mode (no server required). |
| `fastapi` + `uvicorn` | latest | HTTP server for the RAG API. |
| `openai` | latest | OpenAI Python SDK for calling GPT models. |
| `python-dotenv` | latest | Loads environment variables from `.env` file. |

### System tools

| Tool | Purpose |
|------|---------|
| `libreoffice` | Converts `.doc` (legacy Word format) to `.docx`. Install: `sudo apt install libreoffice` |
| `gs` (Ghostscript) | Converts PDFs to PDF/A-2b for long-term archival. Install: `sudo apt install ghostscript` |
| `w3` CLI | Storacha (web3.storage) client for IPFS uploads. Install: `npm install -g @web3-storage/w3cli` |

### OCR engine choice: EasyOCR vs RapidOCR

Both are available in Docling. We chose **EasyOCR** because:

- **Language support:** EasyOCR supports 80+ languages with per-call configuration (`lang=["es", "en"]`). RapidOCR's language parameter is documented as "reserved for future compatibility" -- it currently only uses default Chinese+English models.
- **GPU acceleration:** EasyOCR supports `use_gpu=True` for CUDA acceleration. RapidOCR uses ONNX runtime (CPU only).
- **Accuracy:** EasyOCR uses deep learning models trained per-language, producing better results on Spanish text.

## Overview

The RAG pipeline has 6 steps:

1. **Document Conversion** -- Pre-convert `.doc` → `.docx`, dedup, then extract text into DoclingDocument JSON (Docling)
2. **Chunking** -- Split the text into smaller passages (Docling HybridChunker)
3. **IPFS Sync** -- Upload public files to Storacha (skips `.docx` when `.pdf` exists), record CIDs in `storacha/cids.json`
4. **Embedding** -- Convert each chunk into a vector (BAAI/bge-m3), store CID in payload
5. **Vector Store** -- Store vectors + metadata (incl. IPFS CID) in Qdrant
6. **Query Interface** -- FastAPI RAG API with `/chat` (streaming), `/search`, and OpenAI-compatible `/v1/chat/completions` endpoints

Steps 4 and 5 are combined in `embed_and_index.py`. Steps 3 and 4+5 are independent -- if IPFS sync hasn't been run yet, indexing proceeds without download URLs (they can be added later by re-running step 3 then 4+5).

---

## Step 1: Document Conversion

### Input files

Source folder: `ficheros/` (recursively scans all subfolders)

| Subfolder | Description |
|-----------|-------------|
| `ficheros/publicos/articulos/` | Press articles and reports |
| `ficheros/publicos/AGT.LIBROS/` | Books and longer documents |
| `ficheros/privados/` | Private documents -- RAG only |

| Format | Count | Notes |
|--------|------:|-------|
| `.docx` | 2,568 | Parsed directly by Docling |
| `.pdf` | 880 | Layout analysis + text extraction (OCR if scanned) |
| `.JPG/.jpeg` | 334 | OCR required -- text extracted from images |
| `.doc` | 20 | Pre-step converts to `.docx` in-place with LibreOffice; `.doc` ignored after that |
| `.lnk` / `.URL` | 7 | Shortcuts -- skipped |
| **Total** | **3,809** | |

### Pipeline choice: Standard Pipeline

Docling offers two conversion pipelines:

- **Standard Pipeline** (`StandardPdfPipeline`): Multi-stage, multi-threaded. Uses separate specialized models for OCR, layout detection, table extraction, and assembly. Mature and optimized.
- **VLM Pipeline** (`VlmPipeline`): A single Vision-Language Model processes each page image end-to-end. Newer, better at complex visual layouts, but slower and needs a GPU for good results.

**We chose the Standard Pipeline (GPU-accelerated)** for the following reasons:

1. **Throughput.** With ~3,800 files to process, the Standard pipeline's multi-threaded architecture is significantly faster than VLM's sequential per-page processing.
2. **OCR reliability.** Dedicated OCR engines (EasyOCR, RapidOCR, Tesseract) are more accurate and mature for clean scanned documents than small VLMs. With a CUDA GPU, EasyOCR runs on GPU for faster OCR.
3. **Document type.** The files are press articles and reports -- standard layouts that the traditional pipeline handles well. VLM would be more appropriate for complex infographics or handwritten documents.
4. **GPU usage.** The Standard pipeline uses the GPU to accelerate OCR and layout/table models while keeping VRAM usage low (~500MB-1GB). The VLM pipeline with a model large enough to be accurate (2B+) would consume 4-8GB VRAM and still be slower overall.

**CPU vs GPU:** Converting on CPU produces identical output -- same models, same quality. OCR-heavy files (JPGs, scanned PDFs) are 5-10x slower on CPU. Acceptable for incremental additions; the initial 3,214-file batch would take ~6-10 hours on CPU vs 89.6 min on GPU.

### Conversion script

**Script:** `convert_documents.py`

**Run:** `venv/bin/python convert_documents.py`

**What it does:**

1. **Pre-step:** converts all `.doc` files in `ficheros/` to `.docx` in-place using LibreOffice. The `.docx` becomes the canonical source file; the `.doc` is left untouched but ignored by all subsequent steps. Skips if `.docx` already exists.
2. Recursively scans `ficheros/` for supported files (`.pdf`, `.docx`, `.jpg`, `.jpeg`, `.png`). When the same stem exists as both `.docx` and `.pdf` in the same folder, the `.pdf` is skipped (`.docx` preferred for RAG text quality).
3. **Content dedup:** hashes each file (SHA-256) and skips files with identical content to an earlier file.
4. Initializes a Docling `DocumentConverter` with the Standard Pipeline:
   - **OCR engine:** EasyOCR with GPU acceleration, languages: Spanish + English
   - **Table extraction:** TableFormer in ACCURATE mode
5. Converts each file and saves two output files per document:
   - **`<name>.json`** -- full `DoclingDocument` in JSON format (primary format)
   - **`<name>.md`** -- Markdown export for human inspection only
6. Saves output to `output/`, preserving the subfolder structure
7. **Resumable:** skips files that already have an output `.json` file
8. Saves `output/conversion_report.json` (includes lists of content dupes and docx-preferred skips)

---

## Step 2: Chunking

### What chunking does

Splits each `DoclingDocument` JSON into smaller passages suitable for embedding and retrieval. Chunks carry metadata (source file, page number, section headings) for citations.

### Chunker choice: HybridChunker

- **`HierarchicalChunker`**: Splits strictly by document structure. Ignores token limits.
- **`HybridChunker`**: Combines structure-awareness with a token budget. Merges small adjacent chunks and splits oversized ones while respecting document boundaries.

**We chose `HybridChunker`** because consistent chunk sizes are important for embedding quality.

### Chunking script

**Script:** `chunk_documents.py`

**Run:** `venv/bin/python chunk_documents.py`

**Output format** -- each line in `chunks/chunks.jsonl`:

```json
{
    "text": "El movimiento obrero espanol...",
    "source_file": "publicos/articulos/1967.0418.YA.LA UNIDAD Y LA INDEPENDENCIA SINDICAL_AGT",
    "headings": ["La unidad sindical", "Introduccion"],
    "page": 2,
    "content_labels": ["paragraph", "paragraph"],
    "origin_filename": "1967.0418.YA.LA UNIDAD Y LA INDEPENDENCIA SINDICAL_AGT.pdf",
    "date": "1967-04-18",
    "publication": "YA"
}
```

`source_file` is relative to `output/` with no extension. The `publicos/` or `privados/` prefix is preserved -- this is what downstream scripts use to determine file visibility.

`date` and `publication` are parsed from the filename when it follows the convention `YYYY.MMDD.PUBLICATION.TITLE.ext` (or `YYYY.MM.DD.PUBLICATION.TITLE.ext`). Files that don't match the convention (e.g. book titles) simply omit these fields. Minor typos in filenames (comma or dash instead of dot) are handled. The date uses ISO format: `YYYY-MM-DD`, `YYYY-MM` (day unknown), or `YYYY` (month unknown).

---

## Step 3: IPFS Sync

### Why IPFS / Storacha

Public documents are hosted on IPFS via [Storacha](https://storacha.network/) (web3.storage), backed by Filecoin. This is part of a broader plan to build a permanent digital archive of the author's work. Key properties:

- **Permanent** -- content-addressed by hash (CID). Files never change, so CIDs never expire.
- **Decentralised** -- content is replicated across the Filecoin network and accessible via any IPFS gateway. Survives any single server going down.
- **Integrity** -- the CID is a cryptographic proof that content hasn't changed.
- **Private files stay off IPFS** -- only `ficheros/publicos/` is uploaded. `ficheros/privados/` never leaves the local machine.

### Sync script

**Script:** `sync_to_storacha.py`

**Run:** `venv/bin/python sync_to_storacha.py`

**Storacha account:** Credentials stored in `.env` (not committed).

**Prerequisites:**
```bash
npm install -g @web3-storage/w3cli
w3 login <email>
w3 space use <space-did>
```

**What it does:**

1. Scans `ficheros/publicos/` for all files. Skips `.docx` files when a `.pdf` with the same stem exists in the same folder (the `.pdf` is the original source document).
2. Loads `storacha/cids.json` (the sync state — stores CID + SHA-256 hash per file)
3. Detects **new** files (not in mapping), **modified** files (hash changed), and **deleted** files (in mapping but not on disk)
4. Re-uploads modified files (removes old CID, uploads new version)
5. Uploads new files (`w3 up --no-wrap`) -- saves after each upload, safe to interrupt
6. Removes CIDs from Storacha for deleted files (`w3 rm`). This also cleans up `.docx` files that were previously uploaded but now have a `.pdf` sibling.
7. Saves updated `storacha/cids.json`
8. Uploads the entire `ficheros/publicos/` directory (without `--no-wrap`) to generate a **root directory CID** that wraps all files into a single browsable IPFS directory. Saves to `storacha/root_cid.txt`.

**`--no-wrap`**: uploads the file directly so its CID refers to the file itself. URL: `https://w3s.link/ipfs/<CID>` serves the file directly.

**Root directory CID:** The final step uploads the whole directory to produce a single CID that contains the full folder tree (paths + filenames). This CID can be pinned on another IPFS node (`ipfs pin add <root-cid>`) to recursively pin every file in one operation, adding redundancy. Since IPFS is content-addressed, duplicate files in different folders appear twice in the directory structure but share the same underlying blocks (no storage duplication). The root CID is regenerated on every run that has changes.

**On deletion:** `w3 rm` removes the upload from your Storacha space (stops being served via gateway immediately). Data already committed to Filecoin deals persists until those deals expire (~18 months). For a public archive, this is acceptable.

**`storacha/cids.json`** -- do not delete. If lost, re-running the script is safe (same content = same CID on IPFS), but creates duplicate upload records in your Storacha space.

**After running:** re-run `embed_and_index.py` (after deleting `qdrant_db/`) so the new CIDs are stored in the Qdrant payload.

---

## Step 4+5: Embedding + Indexing

### Embedding model choice: BAAI/bge-m3

| Model | Max tokens | Multilingual | Quality | Notes |
|-------|-----------|-------------|---------|-------|
| `sentence-transformers/all-MiniLM-L6-v2` | 256 | No | Good | English-only |
| `intfloat/multilingual-e5-large` | 512 | Yes | Very good | Lighter than bge-m3 |
| `BAAI/bge-m3` | 8192 | Yes (100+) | **Best** | Top multilingual benchmarks, local GPU. **Chosen.** |
| OpenAI `text-embedding-3-small` | 8191 | Yes | Excellent | API-based, costs money. Not chosen. |

### Embedding + indexing script

**Script:** `embed_and_index.py`

**Run:** `venv/bin/python embed_and_index.py`  (after `sync_to_storacha.py`)

**What it does:**

1. Loads `storacha/cids.json` -- maps public filenames to IPFS CIDs
2. Counts total chunks in `chunks/chunks.jsonl`
3. Creates (or opens) a local Qdrant collection at `qdrant_db/`
4. **Resumable:** skips already-indexed chunks
5. Loads `BAAI/bge-m3` with FP16 on GPU (`devices=["cuda:0"]`)
6. Processes chunks in batches of 256, embeds with `max_length=512`
7. Upserts each batch into Qdrant with full metadata + IPFS info as payload

**PDF fallback for download URLs:** when a chunk comes from a `.docx` (used for better RAG text), `embed_and_index.py` looks up the `.pdf` CID in Storacha first. This way the download URL points to the original PDF source, not the `.docx` transcription.

**Payload stored per point:**

```json
{
    "text":            "El movimiento obrero espanol...",
    "source_file":     "publicos/articulos/1967.0418.YA..._AGT",
    "origin_filename": "1967.0418.YA..._AGT.docx",
    "headings":        ["La unidad sindical"],
    "page":            2,
    "content_labels":  ["paragraph", "paragraph"],
    "date":            "1967-04-18",
    "publication":     "YA",
    "cid":             "bafybei...",
    "download_url":    "https://w3s.link/ipfs/bafybei..."
}
```

`date` and `publication` are parsed from the filename convention (see Step 2). Empty strings when the filename doesn't match.

`cid` and `download_url` are `""` / `null` for `privados/` files and for public files not yet synced to Storacha.

### Vector DB choice: Qdrant

**We chose Qdrant** because:
1. **Metadata filtering** -- combine vector search with filters (e.g. only search `articulos/`).
2. **No infrastructure required** -- embedded mode is as simple as ChromaDB.
3. **Upgrade path** -- one line change to switch to a full Qdrant server.

### Important: Qdrant and concurrent access

Qdrant embedded mode uses RocksDB -- one writer at a time. Do **not** run `embed_and_index.py` while `rag_api.py` is open against the same `qdrant_db/`. Stop the API, re-index, restart.

---

## Step 6: Query Interface

### How it works

`rag_api.py` is a single FastAPI service that handles everything:

1. Loads bge-m3 once at startup (CPU, fp32)
2. Opens Qdrant in embedded mode
3. Initializes an OpenAI client for LLM calls

**Endpoints:**

| Endpoint | Purpose |
|----------|---------|
| `GET /health` | Liveness check -- returns model info, collection name, whether `/chat` is available |
| `POST /search` | Raw chunk retrieval -- returns ranked chunks with metadata and IPFS URLs |
| `POST /chat` | Full RAG pipeline: retrieves chunks, builds prompt, calls LLM, streams the answer back via custom SSE protocol |
| `GET /v1/models` | OpenAI-compatible model list (returns `agt-rag`) |
| `POST /v1/chat/completions` | OpenAI-compatible chat endpoint (for OpenWebUI and other clients) |

### /chat streaming protocol

The `/chat` endpoint accepts conversation history and returns a Server-Sent Events stream:

**Request:**
```json
{
    "messages": [
        {"role": "user", "content": "What did AGT write about democracy?"}
    ],
    "top_k": 5
}
```

**SSE stream:**
```
data: {"type": "sources", "chunks": [...]}     # retrieved chunks (sent first)
data: {"type": "text", "text": "According "}   # LLM output deltas
data: {"type": "text", "text": "to AGT..."}
data: {"type": "error", "message": "..."}      # only on LLM error
data: {"type": "done"}                         # end of stream
```

The `sources` event is sent before any LLM output so the frontend can display source documents while the answer streams in.

### /v1/chat/completions (OpenAI-compatible)

Standard OpenAI chat completions format. RAG retrieval happens transparently -- the last user message is used to search Qdrant, and retrieved chunks are injected into the system prompt before forwarding to the LLM.

Supports both streaming (`"stream": true`) and non-streaming modes. Accepts auth via `x-api-key` header or `Authorization: Bearer <key>`.

### /search endpoint

Lower-level access to the RAG retrieval without the LLM call. Useful for debugging or building custom integrations.

**Request:**
```json
{"question": "democracia representativa", "top_k": 5}
```

**Response:**
```json
{
    "question": "democracia representativa",
    "chunks": [
        {
            "text": "...",
            "source_file": "publicos/articulos/...",
            "origin_filename": "...",
            "page": 3,
            "headings": ["..."],
            "score": 0.82,
            "date": "1996-01-29",
            "publication": "EL MUNDO",
            "cid": "bafybei...",
            "download_url": "https://w3s.link/ipfs/bafybei..."
        }
    ]
}
```

### Configuration

**Environment variables (`.env`):**

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `OPENAI_API_KEY` | For `/chat` | -- | OpenAI API key. If unset, `/chat` and `/v1/chat/completions` return 503; `/search` still works. |
| `RAG_API_KEY` | No | (none) | If set, all endpoints (except `/health`) require auth. `/v1/chat/completions` accepts `x-api-key` header or `Authorization: Bearer`; all other endpoints accept `x-api-key` only. |
| `CORS_ORIGINS` | No | `*` | Comma-separated allowed origins for CORS. |
| `LLM_MODEL` | No | `gpt-4o` | OpenAI model ID (currently set to `gpt-5.4`). |

### File visibility

The `source_file` field in Qdrant reflects the subfolder prefix:
- `publicos/...` -- has `cid` + `download_url` (permanent IPFS link)
- `privados/...` -- `cid=""`, `download_url=null` (citation only, no download)

`download_url` and `cid` are pre-computed at index time by `embed_and_index.py`. No URL logic lives in the API.

### Server setup

```bash
# 1. Install dependencies
python -m venv venv && venv/bin/python -m ensurepip
venv/bin/pip install -r requirements.txt

# 2. Run the processing pipeline (steps 1-5)
venv/bin/python convert_documents.py
venv/bin/python chunk_documents.py
venv/bin/python sync_to_storacha.py
venv/bin/python embed_and_index.py

# 3. Start the API (bge-m3 downloads automatically on first run, ~2.3 GB)
venv/bin/uvicorn rag_api:app --host 0.0.0.0 --port 8000   # reads .env

# 4. Verify
curl http://localhost:8000/health
```

Systemd service (`/etc/systemd/system/rag-api.service`):

```ini
[Unit]
Description=AGT RAG API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/agt
EnvironmentFile=/root/agt/.env
ExecStart=/root/agt/venv/bin/uvicorn rag_api:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### OpenWebUI integration

The API exposes OpenAI-compatible endpoints at `/v1/models` and `/v1/chat/completions`. This allows any OpenAI-compatible client (such as OpenWebUI) to use the RAG pipeline as if it were a regular LLM.

**Setup in OpenWebUI:**

1. Go to **Admin Panel → Settings → Connections**
2. Under **OpenAI API**, add a new connection:
   - **URL:** `http://<server-ip>:8000/v1`
   - **API Key:** the value of `RAG_API_KEY` (or any non-empty string if `RAG_API_KEY` is not set)
3. Save. The model **`agt-rag`** will appear in the model selector.

Every message sent through OpenWebUI is automatically augmented with relevant document chunks before being forwarded to the LLM. The RAG retrieval is transparent to the user.

---

### Workflow for adding new files

```
1. Add files to ficheros/publicos/ or ficheros/privados/
2. venv/bin/python convert_documents.py     # skips already-converted files
3. venv/bin/python chunk_documents.py       # skips already-chunked files
4. venv/bin/python sync_to_storacha.py      # uploads new public files, removes deleted ones
5. systemctl stop rag-api
   rm -rf qdrant_db/
   venv/bin/python embed_and_index.py       # re-indexes with updated CIDs
   systemctl start rag-api
```

### Full pipeline refresh (after code changes)

```bash
rm -rf output/ chunks/ qdrant_db/
venv/bin/python convert_documents.py        # ~90 min on GPU
venv/bin/python chunk_documents.py          # ~2 min
venv/bin/python sync_to_storacha.py         # cleans up old .docx uploads
systemctl stop rag-api                      # or kill uvicorn
venv/bin/python embed_and_index.py          # ~few min
systemctl start rag-api                     # or: venv/bin/uvicorn rag_api:app --host 0.0.0.0 --port 8000
```
