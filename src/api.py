"""
API HTTP para el RFC RAG.
Endpoints:
  GET  /                       — health check
  GET  /rfcs                   — RFCs disponibles en el catálogo
  GET  /rfcs/indexed           — RFCs ya indexados en ChromaDB
  POST /ingest                 — lanza ingesta en background (devuelve job_id)
  GET  /ingest/{job_id}        — estado del job de ingesta
  POST /ask                    — hacer una pregunta
"""

import uuid
from contextlib import asynccontextmanager

import chromadb
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security.api_key import APIKeyHeader
from pydantic import BaseModel

from .config import API_KEY, DB_PATH, EMBED_MODEL, LLM_MODEL, RFCS
from .ingestion import ingest
from .query import SYSTEM_PROMPT, build_context, client, retrieve

_collection: chromadb.Collection | None = None

# Estado en memoria de los jobs de ingesta
_jobs: dict[str, dict] = {}


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
    get_collection()  # inicializar DB al arrancar
    yield


app = FastAPI(
    title="RFC RAG — NaN Builders",
    description="RAG sobre estándares RFC usando el cluster de NaN",
    version="1.0.0",
    lifespan=lifespan,
)


# ── Autenticación ─────────────────────────────────────────────────────────────

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_auth(key: str | None = Depends(_api_key_header)) -> None:
    """Verifica la API key. Si API_KEY no está seteada en el entorno, desactiva la auth."""
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=403, detail="Unauthorized")


# ── Modelos de request/response ───────────────────────────────────────────────

class IngestRequest(BaseModel):
    rfc_ids: list[str] = []
    """Lista de IDs a indexar. Vacío = todos los del catálogo."""


class AskRequest(BaseModel):
    question: str
    rfc_filter: str | None = None
    """Opcional: limitar la búsqueda a un RFC concreto (ej. 'rfc6749')."""
    stream: bool = False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/")
def health():
    col = get_collection()
    return {
        "status":        "ok",
        "chunks_indexed": col.count(),
        "embed_model":   EMBED_MODEL,
        "llm_model":     LLM_MODEL,
    }


@app.get("/rfcs")
def list_rfcs():
    """Devuelve el catálogo completo de RFCs disponibles para indexar."""
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
    """Devuelve los RFCs que ya están en ChromaDB (usa sentinels, O(n_rfcs))."""
    col = get_collection()
    if col.count() == 0:
        return {"indexed": [], "total_chunks": 0}

    results = col.get(
        where={"is_sentinel": {"$eq": True}},
        include=["metadatas"],
    )
    rfc_ids = sorted({m["rfc_id"] for m in results["metadatas"] if "rfc_id" in m})

    return {
        "indexed":      rfc_ids,
        "total_chunks": col.count(),
    }


@app.post("/ingest", status_code=202, dependencies=[Depends(require_auth)])
def ingest_rfcs(req: IngestRequest, background_tasks: BackgroundTasks):
    """
    Lanza la ingesta en background y devuelve un job_id.
    Consulta el estado con GET /ingest/{job_id}.
    """
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
    """Devuelve el estado de un job de ingesta."""
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job no encontrado")
    return job


@app.post("/ask", dependencies=[Depends(require_auth)])
def ask_question(req: AskRequest):
    """Responde una pregunta buscando en los RFCs indexados."""
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
