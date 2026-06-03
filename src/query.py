import chromadb
from rich.console import Console

from .config import client, EMBED_MODEL, LLM_MODEL, TOP_K

console = Console()

SYSTEM_PROMPT = """Eres un experto en estándares técnicos de Internet (RFCs).
Responde ÚNICAMENTE basándote en los fragmentos de RFC proporcionados en el contexto.
Cuando cites información, indica el RFC y la sección de donde proviene.
Si la respuesta no está en el contexto, dilo explícitamente: no inventes información.
Responde en el mismo idioma en que se hace la pregunta."""


def retrieve(query: str, collection: chromadb.Collection,
             rfc_filter: str | None = None) -> list[dict]:
    """
    Busca los TOP_K chunks más relevantes para la query.
    Si se pasa rfc_filter (ej. 'rfc6749') solo busca en ese RFC.
    Los sentinels se excluyen con el filtro is_sentinel=False.
    """
    q_embed = client.embeddings.create(
        model=EMBED_MODEL,
        input=[query],
    ).data[0].embedding

    # Filtro base: excluir sentinels
    base_filter: dict = {"is_sentinel": {"$eq": False}}
    if rfc_filter:
        where = {"$and": [base_filter, {"rfc_id": {"$eq": rfc_filter}}]}
    else:
        where = base_filter

    results = collection.query(
        query_embeddings=[q_embed],
        n_results=TOP_K,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    chunks = []
    for doc, meta, dist in zip(
        results["documents"][0],
        results["metadatas"][0],
        results["distances"][0],
    ):
        chunks.append({
            "text":          doc,
            "rfc_id":        meta.get("rfc_id", ""),
            "title":         meta.get("title", ""),
            "section_title": meta.get("section_title", ""),
            "score":         round(1 - dist, 3),  # distancia coseno → similitud
        })

    return chunks


def build_context(chunks: list[dict]) -> str:
    """Formatea los chunks recuperados como contexto para el LLM."""
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(
            f"[Fragmento {i} — {c['rfc_id'].upper()} · {c['title']} "
            f"· Sección: {c['section_title']} · Relevancia: {c['score']}]\n"
            f"{c['text']}"
        )
    return "\n\n---\n\n".join(parts)


def ask(question: str, collection: chromadb.Collection,
        rfc_filter: str | None = None,
        stream: bool = True) -> str:
    """
    Pipeline completo: recupera chunks relevantes y genera respuesta con el LLM.
    Con stream=True imprime la respuesta en tiempo real (CLI).
    """
    chunks = retrieve(question, collection, rfc_filter)

    if not chunks:
        return "No encontré información relevante en los RFCs indexados."

    console.print("\n[dim]Fuentes recuperadas:[/dim]")
    for c in chunks:
        console.print(
            f"  [dim]· {c['rfc_id'].upper()} — {c['section_title'][:60]} "
            f"(score: {c['score']})[/dim]"
        )
    console.print()

    context = build_context(chunks)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": (
            f"Contexto de los RFCs:\n\n{context}\n\n"
            f"Pregunta: {question}"
        )},
    ]

    if stream:
        full_response = ""
        # openai SDK: create() con stream=True devuelve un iterador de chunks
        stream_iter = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=2048,
            stream=True,
        )
        for chunk in stream_iter:
            text = chunk.choices[0].delta.content or ""
            if text:
                print(text, end="", flush=True)
                full_response += text
        print()
        return full_response
    else:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=messages,
            temperature=0.1,
            max_tokens=2048,
        )
        return resp.choices[0].message.content
