import re
import time
import requests
import chromadb
from rich.console import Console
from rich.progress import track

from .config import client, EMBED_MODEL, CHUNK_SIZE, CHUNK_OVERLAP, RFCS

console = Console()


# ── Descarga ──────────────────────────────────────────────────────────────────

def fetch_rfc(rfc_id: str) -> str:
    """Descarga el texto plano de un RFC desde rfc-editor.org."""
    url = RFCS[rfc_id]["url"]
    console.print(f"  [cyan]↓[/cyan] Descargando {rfc_id} desde {url}")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.text


# ── Chunking por sección ───────────────────────────────────────────────────────

def split_by_sections(text: str, rfc_id: str) -> list[dict]:
    """
    Divide el RFC en secciones reales (ej. '1.', '1.1.', '2.') más que
    en ventanas fijas. El preámbulo (Abstract, Status, Copyright) antes
    de la sección 1 también se indexa como chunk propio.
    Si una sección es muy larga la subdivide con overlap.
    """
    section_pattern = re.compile(r'(?m)^(\d+(?:\.\d+)*\.?\s{2,}.+)$')
    sections = []
    positions = [m.start() for m in section_pattern.finditer(text)]

    # Incluir el preámbulo (todo lo anterior a la primera sección numerada)
    if positions and positions[0] > 0:
        preamble = text[:positions[0]].strip()
        if len(preamble) >= 50:
            for sub in _subdivide(preamble, rfc_id, "Preamble"):
                sections.append(sub)

    positions.append(len(text))  # sentinel de fin

    for i in range(len(positions) - 1):
        section_text = text[positions[i]:positions[i + 1]].strip()
        if len(section_text) < 50:
            continue
        first_line = section_text.splitlines()[0].strip()
        for sub in _subdivide(section_text, rfc_id, first_line):
            sections.append(sub)

    return sections


def _subdivide(text: str, rfc_id: str, section_title: str) -> list[dict]:
    """Divide un bloque grande en sub-chunks con overlap (en caracteres)."""
    char_size    = CHUNK_SIZE * 4        # aprox 4 chars/token
    char_overlap = CHUNK_OVERLAP * 4
    chunks = []
    start = 0
    idx   = 0

    while start < len(text):
        end   = min(start + char_size, len(text))
        # Prefijamos el título de sección para que el retrieval tenga contexto
        chunk = f"[{section_title}]\n{text[start:end].strip()}"
        if chunk:
            chunks.append({
                "rfc_id":        rfc_id,
                "section_title": section_title,
                "chunk_index":   idx,
                "text":          chunk,
            })
            idx += 1
        start += char_size - char_overlap

    return chunks


# ── Embedding ──────────────────────────────────────────────────────────────────

def embed(texts: list[str], retries: int = 3) -> list[list[float]]:
    """Llama al endpoint de embeddings de NaN con reintentos exponenciales."""
    for attempt in range(retries):
        try:
            resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
            return [item.embedding for item in resp.data]
        except Exception as exc:
            if attempt == retries - 1:
                raise
            wait = 2 ** attempt
            console.print(f"  [yellow]⚠ Error en embed (intento {attempt+1}), reintentando en {wait}s: {exc}[/yellow]")
            time.sleep(wait)


# ── Ingestion completa ─────────────────────────────────────────────────────────

def ingest(rfc_ids: list[str], collection: chromadb.Collection) -> None:
    """
    Descarga, trocea, embebe e indexa en ChromaDB los RFCs indicados.
    Si un chunk ya existe (mismo ID) lo sobreescribe.
    """
    for rfc_id in rfc_ids:
        meta = RFCS[rfc_id]
        console.print(f"\n[bold]→ Indexando {rfc_id}[/bold] — {meta['title']}")

        raw    = fetch_rfc(rfc_id)
        chunks = split_by_sections(raw, rfc_id)
        console.print(f"  [green]{len(chunks)} chunks generados[/green]")

        batch_size = 32
        for i in track(range(0, len(chunks), batch_size),
                        description=f"  Embedding {rfc_id}"):
            batch  = chunks[i:i + batch_size]
            texts  = [c["text"] for c in batch]
            embeds = embed(texts)

            collection.upsert(
                ids        = [f"{rfc_id}_{c['chunk_index']:04d}" for c in batch],
                embeddings = embeds,
                documents  = texts,
                metadatas  = [{
                    "rfc_id":        c["rfc_id"],
                    "section_title": c["section_title"],
                    "title":         meta["title"],
                    "is_sentinel":   False,
                } for c in batch],
            )

        # Upsert de sentinel para /rfcs/indexed (no requiere embedding)
        collection.upsert(
            ids       = [f"{rfc_id}__sentinel"],
            documents = [meta["title"]],
            metadatas = [{"rfc_id": rfc_id, "title": meta["title"], "is_sentinel": True}],
        )

        console.print(f"  [bold green]✓ {rfc_id} indexado[/bold green]")
