"""
API HTTP para el RFC RAG.
Endpoints:
  GET  /                       — health check
  GET  /rfcs                   — RFCs disponibles en el catálogo
  GET  /rfcs/indexed           — RFCs ya indexados en ChromaDB
  POST /ingest                 — lanza ingesta en background (devuelve job_id)
  GET  /ingest/{job_id}        — estado del job de ingesta
  DELETE /collection           — resetea la colección ChromaDB
  POST /ask                    — hacer una pregunta
"""

import os
import uuid
from contextlib import asynccontextmanager

import chromadb
from fastapi import BackgroundTasks, Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.security.api_key import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .config import API_KEY, DB_PATH, EMBED_MODEL, LLM_MODEL, RFCS
from .ingestion import ingest, ingest_pdf
from .query import SYSTEM_PROMPT, build_context, client, retrieve

_collection: chromadb.Collection | None = None
_jobs: dict[str, dict] = {}

limiter = Limiter(key_func=get_remote_address)


def get_collection() -> chromadb.Collection:
    global _collection
    if _collection is None:
        db = chromadb.PersistentClient(path=DB_PATH)
        _collection = db.get_or_create_collection(
            name="rfcs",
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_collection()
    yield


app = FastAPI(
    title="RFC RAG — NaN Builders",
    description="RAG sobre estándares RFC usando el cluster de NaN",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# Evitar que el navegador cachee el HTML/JS del frontend de forma agresiva.
# Sin esto, los usuarios siguen ejecutando una versión vieja del index.html
# aunque el servidor ya sirva una nueva tras un redeploy.
@app.middleware("http")
async def _no_cache_frontend(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/ui"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Servir el frontend desde /ui si el directorio existe
_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/ui", StaticFiles(directory=_frontend_dir, html=True), name="frontend")


# ── Autenticación (solo para endpoints de administración) ─────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_auth(key: str | None = Depends(_api_key_header)) -> None:
    """Protege endpoints admin. Sin efecto si API_KEY no está seteada."""
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized")


# ── Modelos de request/response ───────────────────────────────────────────────

class IngestRequest(BaseModel):
    rfc_ids: list[str] = []


class AskRequest(BaseModel):
    question: str
    rfc_filter: str | None = None
    stream: bool = False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def health():
    col = get_collection()
    return {
        "status":         "ok",
        "chunks_indexed": col.count(),
        "embed_model":    EMBED_MODEL,
        "llm_model":      LLM_MODEL,
    }


@app.get("/rfcs")
def list_rfcs():
    return {
        rfc_id: {
            "title":       meta["title"],
            "description": meta["description"],
            "url":         meta["url"],
        }
        for rfc_id, meta in RFCS.items()
    }


@app.get("/rfcs/indexed")
def indexed_rfcs():
    col = get_collection()
    if col.count() == 0:
        return {"indexed": [], "total_chunks": 0}

    results = col.get(limit=col.count(), include=["metadatas"])
    rfc_ids = sorted({m["rfc_id"] for m in results["metadatas"] if "rfc_id" in m})
    return {"indexed": rfc_ids, "total_chunks": col.count()}


@app.delete("/collection", dependencies=[Depends(require_auth)])
def reset_collection():
    """Admin: borra y recrea la colección ChromaDB."""
    global _collection
    db = chromadb.PersistentClient(path=DB_PATH)
    db.delete_collection("rfcs")
    _collection = db.get_or_create_collection(
        name="rfcs",
        metadata={"hnsw:space": "cosine"},
    )
    return {"status": "ok", "message": "Colección reseteada"}


@app.post("/ingest", status_code=202, dependencies=[Depends(require_auth)])
def ingest_rfcs(req: IngestRequest, background_tasks: BackgroundTasks):
    """Admin: lanza ingesta en background y devuelve un job_id."""
    rfc_ids = req.rfc_ids or list(RFCS.keys())

    invalid = [r for r in rfc_ids if r not in RFCS]
    if invalid:
        raise HTTPException(
            status_code=400,
            detail=f"RFCs no reconocidos: {invalid}. Disponibles: {list(RFCS.keys())}",
        )

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "rfc_ids": rfc_ids}

    def run_ingest():
        try:
            ingest(rfc_ids, get_collection())
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["total_chunks"] = get_collection().count()
        except Exception as exc:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["detail"] = str(exc)

    background_tasks.add_task(run_ingest)
    return {"job_id": job_id, "status": "accepted", "rfc_ids": rfc_ids}


@app.get("/ingest/{job_id}", dependencies=[Depends(require_auth)])
def ingest_status(job_id: str):
    """Admin: estado de un job de ingesta."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


@app.get("/documents")
def list_documents():
    """
    Devuelve los PDFs subidos manualmente que ya están indexados en ChromaDB.
    Los RFCs del catálogo no aparecen aquí; usa GET /rfcs/indexed para ellos.
    """
    col = get_collection()
    if col.count() == 0:
        return {"documents": []}

    results = col.get(
        where={"source_type": {"$eq": "pdf"}},
        include=["metadatas"],
    )

    seen: dict[str, dict] = {}
    for m in results["metadatas"]:
        doc_id = m.get("rfc_id", "")
        if doc_id and doc_id not in seen:
            seen[doc_id] = {
                "doc_id":   doc_id,
                "filename": m.get("filename", ""),
                "title":    m.get("title", ""),
            }

    return {"documents": list(seen.values())}


@app.post("/documents/upload", dependencies=[Depends(require_auth)])
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = None,
):
    """
    Sube e indexa un PDF en ChromaDB.
    El doc_id se deriva del nombre del fichero (ej. 'my_report.pdf' → 'pdf_my_report').
    Usa ese doc_id como valor de rfc_filter en POST /ask para acotar la búsqueda.

    - Autenticación: requiere X-API-Key header.
    - Límite de tamaño: 50 MB.
    - Solo PDFs (content-type application/pdf o extensión .pdf).
    """
    MAX_SIZE = 50 * 1024 * 1024  # 50 MB

    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Solo se aceptan ficheros .pdf")

    pdf_bytes = await file.read()
    if len(pdf_bytes) > MAX_SIZE:
        raise HTTPException(status_code=413, detail="El fichero supera el límite de 50 MB")
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=400, detail="El fichero está vacío")

    try:
        result = ingest_pdf(pdf_bytes, file.filename, get_collection(), title=title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {
        "status":   "ok",
        "doc_id":   result["doc_id"],
        "filename": result["filename"],
        "title":    result["title"],
        "pages":    result["pages"],
        "chunks":   result["chunks"],
        "hint":     f"Usa rfc_filter: \"{result['doc_id']}\" en POST /ask para buscar solo en este documento",
    }


@app.post("/ask")
@limiter.limit("5/minute;100/day")
def ask_question(request: Request, req: AskRequest):
    """Responde una pregunta. Límite: 5 req/min y 100 req/día por IP."""
    col = get_collection()

    if col.count() == 0:
        raise HTTPException(
            status_code=400,
            detail="La base de datos está vacía. Llama a POST /ingest primero.",
        )

    chunks = retrieve(req.question, col, rfc_filter=req.rfc_filter)
    if not chunks:
        raise HTTPException(status_code=404, detail="No se encontraron fragmentos relevantes.")

    context = build_context(chunks)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": (
            f"Contexto de los RFCs:\n\n{context}\n\n"
            f"Pregunta: {req.question}"
        )},
    ]

    sources = [
        {
            "rfc_id":        c["rfc_id"],
            "title":         c["title"],
            "section_title": c["section_title"],
            "score":         c["score"],
        }
        for c in chunks
    ]

    if req.stream:
        def generate():
            for chunk in client.chat.completions.create(
                model=LLM_MODEL,
                messages=messages,
                temperature=0.1,
                max_tokens=2048,
                stream=True,
            ):
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    yield delta

        return StreamingResponse(generate(), media_type="text/plain")

    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0.1,
        max_tokens=2048,
    )

    return {
        "answer":  resp.choices[0].message.content,
        "sources": sources,
    }
