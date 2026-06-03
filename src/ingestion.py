import re
import time
import unicodedata
import requests
import chromadb
import fitz  # pymupdf
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
        # Renumerar globalmente para garantizar IDs únicos entre secciones
        for i, chunk in enumerate(chunks):
            chunk["chunk_index"] = i
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
                } for c in batch],
            )

        console.print(f"  [bold green]✓ {rfc_id} indexado[/bold green]")


# ── Ingestion de PDFs ──────────────────────────────────────────────────────────

def _sanitize_doc_id(filename: str) -> str:
    """Convierte un nombre de fichero en un doc_id seguro para ChromaDB."""
    name = filename.rsplit(".", 1)[0]          # quitar extensión
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")
    return f"pdf_{name[:48]}"                  # prefijo para distinguir de RFCs


def _extract_text_from_pdf(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """
    Extrae texto de un PDF página a página.
    Devuelve lista de (page_number, text).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        text = page.get_text("text")
        if text.strip():
            pages.append((page.number + 1, text))
    doc.close()
    return pages


def _split_pdf_into_chunks(pages: list[tuple[int, str]], doc_id: str,
                            filename: str, title: str) -> list[dict]:
    """
    Chunking de PDF por párrafos con ventana deslizante.
    Respeta los saltos de párrafo (doble newline) para no cortar ideas a mitad.
    """
    char_size    = CHUNK_SIZE * 4
    char_overlap = CHUNK_OVERLAP * 4
    chunks = []
    idx    = 0

    for page_num, page_text in pages:
        # Dividir por párrafos y recombinar hasta llegar a char_size
        paragraphs = [p.strip() for p in re.split(r'\n{2,}', page_text) if p.strip()]
        buffer = ""

        for para in paragraphs:
            if len(buffer) + len(para) + 1 > char_size and buffer:
                chunk_text = f"[{title} · Página {page_num}]\n{buffer.strip()}"
                chunks.append({
                    "doc_id":        doc_id,
                    "rfc_id":        doc_id,   # rfc_id actúa como source_id genérico
                    "section_title": f"Página {page_num}",
                    "chunk_index":   idx,
                    "filename":      filename,
                    "title":         title,
                    "page":          page_num,
                    "source_type":   "pdf",
                    "text":          chunk_text,
                })
                idx += 1
                # Overlap: conservar los últimos char_overlap caracteres del buffer
                buffer = buffer[-char_overlap:] + "\n" + para
            else:
                buffer = (buffer + "\n" + para).strip()

        # Flush del buffer al final de la página
        if buffer.strip():
            chunk_text = f"[{title} · Página {page_num}]\n{buffer.strip()}"
            chunks.append({
                "doc_id":        doc_id,
                "rfc_id":        doc_id,
                "section_title": f"Página {page_num}",
                "chunk_index":   idx,
                "filename":      filename,
                "title":         title,
                "page":          page_num,
                "source_type":   "pdf",
                "text":          chunk_text,
            })
            idx += 1

    return chunks


def ingest_pdf(pdf_bytes: bytes, filename: str,
               collection: chromadb.Collection,
               title: str | None = None) -> dict:
    """
    Extrae texto de un PDF, lo trocea, genera embeddings y lo indexa en ChromaDB.
    Devuelve metadatos del resultado: doc_id, filename, chunks indexados.

    - doc_id: identificador derivado del nombre del fichero (ej. pdf_my_report)
    - Usa rfc_id = doc_id para que el filtro rfc_filter de /ask funcione igual
    - source_type = "pdf" permite distinguirlos de RFCs en /documents
    """
    doc_id       = _sanitize_doc_id(filename)
    display_title = title or filename.rsplit(".", 1)[0]

    console.print(f"\n[bold]→ Indexando PDF[/bold] — {filename} (doc_id: {doc_id})")

    pages  = _extract_text_from_pdf(pdf_bytes)
    chunks = _split_pdf_into_chunks(pages, doc_id, filename, display_title)
    console.print(f"  [green]{len(pages)} páginas, {len(chunks)} chunks generados[/green]")

    if not chunks:
        raise ValueError(f"No se pudo extraer texto de '{filename}'. ¿Es un PDF escaneado sin OCR?")

    batch_size = 32
    for i in track(range(0, len(chunks), batch_size),
                    description=f"  Embedding {doc_id}"):
        batch  = chunks[i:i + batch_size]
        texts  = [c["text"] for c in batch]
        embeds = embed(texts)

        collection.upsert(
            ids        = [f"{doc_id}_{c['chunk_index']:04d}" for c in batch],
            embeddings = embeds,
            documents  = texts,
            metadatas  = [{
                "rfc_id":        c["rfc_id"],
                "section_title": c["section_title"],
                "title":         c["title"],
                "filename":      c["filename"],
                "page":          c["page"],
                "source_type":   c["source_type"],
            } for c in batch],
        )

    console.print(f"  [bold green]✓ {filename} indexado como {doc_id}[/bold green]")
    return {
        "doc_id":   doc_id,
        "filename": filename,
        "title":    display_title,
        "pages":    len(pages),
        "chunks":   len(chunks),
    }
