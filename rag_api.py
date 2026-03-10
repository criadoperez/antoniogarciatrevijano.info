"""
RAG API — single service for retrieval, LLM calls, and streaming responses.

Runs on agt.criadoperez.com. The static frontend (hosted on IPFS) sends
questions here; the API retrieves relevant document chunks from Qdrant,
calls the OpenAI API with the context, and streams the answer back.

Run:
    uvicorn rag_api:app --host 0.0.0.0 --port 8000

Endpoints:
    GET  /health   — liveness check
    POST /search   — raw chunk retrieval (returns ranked chunks with metadata)
    POST /chat     — RAG retrieval + LLM streaming response (custom SSE protocol)
    GET  /v1/models              — OpenAI-compatible model list
    POST /v1/chat/completions    — OpenAI-compatible chat (for OpenWebUI etc.)

Environment variables (loaded from .env if present):
    OPENAI_API_KEY     — required for /chat endpoint
    RAG_API_KEY        — optional; if set, requires x-api-key header on requests
    CORS_ORIGINS       — comma-separated allowed origins (default: *)
    LLM_MODEL          — OpenAI model ID (default: gpt-4o)

Dependencies:
    pip install fastapi uvicorn FlagEmbedding qdrant-client==1.16.2 openai python-dotenv
"""

import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from FlagEmbedding import BGEM3FlagModel
from openai import OpenAI
from pydantic import BaseModel
from qdrant_client import QdrantClient

# ── Configuration ──────────────────────────────────────────────────────

QDRANT_PATH     = Path("qdrant_db")
COLLECTION      = "documents"
EMBEDDING_MODEL = "BAAI/bge-m3"
LLM_MODEL       = os.getenv("LLM_MODEL", "gpt-4o")
MIN_SCORE       = 0.3    # cosine similarity floor; raise to tighten relevance
API_KEY         = os.getenv("RAG_API_KEY", "")   # empty = no auth required
CORS_ORIGINS    = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]

# ── Logging ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Global state (populated at startup) ───────────────────────────────

_model: BGEM3FlagModel | None = None
_qdrant: QdrantClient | None = None
_openai: OpenAI | None = None


# ── Startup / shutdown ─────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(_app: FastAPI):
    global _model, _qdrant, _openai

    log.info("Loading embedding model: %s (CPU, fp32) …", EMBEDDING_MODEL)
    _model = BGEM3FlagModel(EMBEDDING_MODEL, use_fp16=False, devices=["cpu"])
    log.info("Embedding model ready.")

    if not QDRANT_PATH.exists():
        raise RuntimeError(
            f"Qdrant DB not found at {QDRANT_PATH}. "
            "Run embed_and_index.py before starting the API."
        )
    log.info("Opening Qdrant at %s …", QDRANT_PATH)
    _qdrant = QdrantClient(path=str(QDRANT_PATH))
    n = _qdrant.count(COLLECTION).count
    log.info("Qdrant ready — %d points in collection '%s'.", n, COLLECTION)

    if os.getenv("OPENAI_API_KEY"):
        _openai = OpenAI()
        log.info("OpenAI client initialized (model: %s).", LLM_MODEL)
    else:
        log.warning("OPENAI_API_KEY not set — /chat endpoint will return 503.")

    if API_KEY:
        log.info("API key authentication is ENABLED.")
    else:
        log.warning("RAG_API_KEY is not set — endpoints are open to anyone.")

    yield  # ── server runs ────────────────────────────────────────────

    log.info("Shutdown.")


app = FastAPI(title="AGT RAG API", version="2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


# ── Schemas ────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    question: str
    top_k: int = 5


class ChunkResult(BaseModel):
    text: str
    source_file: str        # e.g. "publicos/articulos/foo" or "privados/..."
    origin_filename: str    # original filename with extension, e.g. "foo.pdf"
    page: int | None
    headings: list[str]
    score: float
    date: str               # ISO date parsed from filename, e.g. "1996-01-29" — empty if not available
    publication: str        # publication name parsed from filename — empty if not available
    cid: str                # IPFS CID — empty string if private or not yet synced
    download_url: str | None  # permanent IPFS URL — null if private or not yet synced


class SearchResponse(BaseModel):
    question: str
    chunks: list[ChunkResult]


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    top_k: int = 5


# ── Shared helpers ─────────────────────────────────────────────────────

def _check_auth(x_api_key: str):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _embed_text(text: str) -> list[float]:
    output = _model.encode(
        [text],
        batch_size=1,
        max_length=512,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    )
    return output["dense_vecs"][0].tolist()


def _search_chunks(question: str, top_k: int) -> list[ChunkResult]:
    vector = _embed_text(question)
    result = _qdrant.query_points(
        collection_name=COLLECTION,
        query=vector,
        limit=top_k,
        with_payload=True,
        score_threshold=MIN_SCORE,
    )
    chunks = []
    for hit in result.points:
        p = hit.payload or {}
        chunks.append(ChunkResult(
            text=p.get("text", ""),
            source_file=p.get("source_file", ""),
            origin_filename=p.get("origin_filename", ""),
            page=p.get("page"),
            headings=p.get("headings") or [],
            score=round(hit.score, 4),
            date=p.get("date", ""),
            publication=p.get("publication", ""),
            cid=p.get("cid", ""),
            download_url=p.get("download_url"),
        ))
    return chunks


def _build_system_prompt(chunks: list[ChunkResult]) -> str:
    """Build a system prompt with RAG context for the LLM."""
    base = (
        "Eres un asistente experto en los escritos y el pensamiento de "
        "Antonio Garcia-Trevijano. "
        "Responde en el idioma en que te pregunten."
    )

    if not chunks:
        return (
            base
            + " No se han encontrado fragmentos relevantes en la base de documentos. "
            "Responde usando tu conocimiento general e indica que no encontraste "
            "referencias especificas en los documentos."
        )

    lines = [
        base,
        "",
        "Basa tus respuestas en los fragmentos de documentos proporcionados "
        "cuando sean relevantes. Si la informacion no esta en los fragmentos, "
        "puedes usar tu conocimiento general, pero indica claramente cuando lo haces.",
        "",
        "---",
        "",
        "## Contexto recuperado de los documentos",
        "",
    ]

    for i, chunk in enumerate(chunks, 1):
        filename = chunk.origin_filename or chunk.source_file or "desconocido"
        header = f"**Fragmento {i}**"
        if chunk.date or chunk.publication:
            parts = []
            if chunk.date:
                parts.append(chunk.date)
            if chunk.publication:
                parts.append(chunk.publication)
            header += f" -- {', '.join(parts)}"
        header += f" -- `{filename}`"
        if chunk.page:
            header += f", pagina {chunk.page}"
        if chunk.headings:
            header += f" | {' > '.join(chunk.headings)}"
        header += f"  *(relevancia: {chunk.score:.2f})*"

        lines.append(header)
        lines.append("")
        lines.append(chunk.text)
        lines.append("")
        if chunk.download_url:
            lines.append(f"Descargar documento: {chunk.download_url}")
            lines.append("")
        lines.append("---")
        lines.append("")

    lines.append(
        "Al final de tu respuesta incluye una seccion `## Fuentes` listando "
        "los documentos que usaste, con nombre de archivo, numero de pagina "
        "y enlace de descarga cuando este disponible."
    )

    return "\n".join(lines)


# ── Endpoints ──────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Liveness probe. Returns 200 once the model and DB are loaded."""
    return {
        "status": "ok",
        "collection": COLLECTION,
        "model": EMBEDDING_MODEL,
        "llm": LLM_MODEL,
        "chat_available": _openai is not None,
    }


@app.post("/search", response_model=SearchResponse)
def search(
    req: SearchRequest,
    x_api_key: str = Header(default=""),
):
    _check_auth(x_api_key)

    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    if _model is None or _qdrant is None:
        raise HTTPException(status_code=503, detail="Service initializing — try again shortly")

    chunks = _search_chunks(req.question, req.top_k)

    log.info(
        "search | top_k=%d | returned=%d | q=%r",
        req.top_k, len(chunks), req.question[:80],
    )
    return SearchResponse(question=req.question, chunks=chunks)


@app.post("/chat")
async def chat(
    req: ChatRequest,
    x_api_key: str = Header(default=""),
):
    _check_auth(x_api_key)

    if _openai is None:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY not configured — /chat is unavailable",
        )

    if _model is None or _qdrant is None:
        raise HTTPException(status_code=503, detail="Service initializing — try again shortly")

    # Extract the last user message for RAG retrieval
    query = ""
    for msg in reversed(req.messages):
        if msg.role == "user":
            query = msg.content.strip()
            break

    if not query:
        raise HTTPException(status_code=400, detail="No user message found")

    # RAG retrieval
    chunks = _search_chunks(query, req.top_k)
    system_prompt = _build_system_prompt(chunks)

    # Prepare messages for OpenAI
    messages = [{"role": "system", "content": system_prompt}]
    messages += [
        {"role": m.role, "content": m.content}
        for m in req.messages
        if m.role != "system"
    ]

    log.info("chat | chunks=%d | q=%r", len(chunks), query[:80])

    def generate():
        # Send sources first so the frontend can display them while streaming
        sources = [
            {
                "origin_filename": c.origin_filename,
                "source_file": c.source_file,
                "page": c.page,
                "headings": c.headings,
                "score": c.score,
                "date": c.date,
                "publication": c.publication,
                "download_url": c.download_url,
            }
            for c in chunks
        ]
        yield f"data: {json.dumps({'type': 'sources', 'chunks': sources})}\n\n"

        # Stream LLM response
        try:
            stream = _openai.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                max_completion_tokens=4096,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield f"data: {json.dumps({'type': 'text', 'text': delta.content})}\n\n"
        except Exception as exc:
            log.error("LLM streaming error: %s", exc)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")


# ── OpenAI-compatible endpoints (for OpenWebUI) ──────────────────────

RAG_MODEL_ID = "agt-rag"


@app.get("/v1/models")
def list_models(x_api_key: str = Header(default="")):
    _check_auth(x_api_key)
    return {
        "object": "list",
        "data": [
            {
                "id": RAG_MODEL_ID,
                "object": "model",
                "created": 0,
                "owned_by": "agt",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def openai_chat_completions(
    request: dict,
    x_api_key: str = Header(default=""),
    authorization: str = Header(default=""),
):
    # Accept auth via x-api-key or Bearer token (OpenWebUI sends Authorization header)
    token = x_api_key
    if not token and authorization.startswith("Bearer "):
        token = authorization[7:]
    _check_auth(token)

    if _openai is None:
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY not configured — chat is unavailable",
        )
    if _model is None or _qdrant is None:
        raise HTTPException(status_code=503, detail="Service initializing — try again shortly")

    messages = request.get("messages", [])
    stream = request.get("stream", False)

    # Extract last user message for RAG retrieval
    query = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            content = msg.get("content", "")
            query = content.strip() if isinstance(content, str) else ""
            break

    if not query:
        raise HTTPException(status_code=400, detail="No user message found")

    # RAG retrieval
    chunks = _search_chunks(query, 5)
    system_prompt = _build_system_prompt(chunks)

    # Build augmented messages: inject RAG system prompt, drop any existing system messages
    augmented = [{"role": "system", "content": system_prompt}]
    augmented += [m for m in messages if m.get("role") != "system"]

    log.info("v1/chat/completions | chunks=%d | stream=%s | q=%r", len(chunks), stream, query[:80])

    completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    if not stream:
        response = _openai.chat.completions.create(
            model=LLM_MODEL,
            messages=augmented,
            max_completion_tokens=4096,
        )
        text = response.choices[0].message.content or ""
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": RAG_MODEL_ID,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }

    def generate_openai():
        try:
            openai_stream = _openai.chat.completions.create(
                model=LLM_MODEL,
                messages=augmented,
                max_completion_tokens=4096,
                stream=True,
            )
            for chunk in openai_stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                if delta and delta.content:
                    yield (
                        f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': RAG_MODEL_ID, 'choices': [{'index': 0, 'delta': {'content': delta.content}, 'finish_reason': None}]})}\n\n"
                    )
            yield (
                f"data: {json.dumps({'id': completion_id, 'object': 'chat.completion.chunk', 'created': created, 'model': RAG_MODEL_ID, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
            )
        except Exception as exc:
            log.error("v1/chat/completions streaming error: %s", exc)
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate_openai(), media_type="text/event-stream")
