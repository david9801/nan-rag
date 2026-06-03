# CLAUDE.md — Guía para desarrolladores

Referencia técnica del proyecto **RFC RAG** para quien trabaje en el código.

---

## Stack

| Capa | Tecnología |
|------|-----------|
| Embeddings | `qwen3-embedding` vía API NaN Builders (compatible OpenAI SDK) |
| LLM | `deepseek-v4-flash` vía API NaN Builders |
| Vector DB | ChromaDB PersistentClient (SQLite + HNSW local) |
| API | FastAPI + uvicorn, puerto 3000 |
| Rate limiting | slowapi (por IP) |
| PDF parsing | pymupdf (`fitz`) |
| Deploy | Docker en NaN Cloud, linked a la rama `main` de GitHub |

---

## Estructura del proyecto

```
nan-rag/
├── Dockerfile
├── requirements.txt
├── setup.sh                  # instalación rápida sin Docker
├── .env.example              # plantilla de variables de entorno
├── data/
│   └── chroma_db/            # ChromaDB persistente (excluida del repo por .gitignore)
└── src/
    ├── __init__.py           # convierte src/ en paquete Python
    ├── config.py             # constantes, cliente OpenAI, catálogo RFCS, DB_PATH
    ├── ingestion.py          # fetch, chunking, embedding, upsert en ChromaDB
    ├── query.py              # retrieve (embed query + búsqueda coseno) + generación LLM
    ├── api.py                # FastAPI: endpoints HTTP, auth, rate limiting
    └── main.py               # CLI: ingest / ask / list
```

---

## Variables de entorno

| Variable | Obligatoria | Default | Descripción |
|----------|-------------|---------|-------------|
| `NAN_API_KEY` | Sí | — | Clave de acceso al endpoint NaN Builders |
| `NAN_BASE_URL` | No | `https://api.nan.builders/v1` | Endpoint compatible OpenAI |
| `EMBED_MODEL` | No | `qwen3-embedding` | Modelo de embeddings |
| `LLM_MODEL` | No | `deepseek-v4-flash` | Modelo de generación |
| `API_KEY` | No | `""` | Protege endpoints admin. Vacío = auth desactivada |
| `DB_PATH` | No | `<repo_root>/data/chroma_db` | Ruta de ChromaDB. En Docker: `/app/data/chroma_db` |

---

## Ejecución local

```bash
# Instalar dependencias
bash setup.sh

# Configurar entorno
cp .env.example .env
# Editar .env con NAN_API_KEY

# CLI
python -m src.main list
python -m src.main ingest rfc6749 rfc7519
python -m src.main ask "¿Qué es PKCE?"

# API local
uvicorn src.api:app --host 0.0.0.0 --port 3000 --reload
```

---

## Arquitectura de datos en ChromaDB

Todos los documentos (RFCs y PDFs) conviven en una única colección llamada `rfcs`
con métrica coseno (`hnsw:space: cosine`).

### Campos de metadata comunes

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `rfc_id` | str | Identificador del documento. RFCs: `rfc6749`. PDFs: `pdf_<nombre>` |
| `title` | str | Título legible del documento |
| `section_title` | str | Título de sección (RFCs) o `Página N` (PDFs) |
| `source_type` | str | `"pdf"` para PDFs subidos. Ausente en RFCs (compatibilidad) |

### Campos adicionales en PDFs

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `filename` | str | Nombre original del fichero |
| `page` | int | Número de página de origen |
| `doc_id` | str | Igual que `rfc_id`, copia explícita |

### IDs de chunks

- RFCs: `{rfc_id}_{chunk_index:04d}` → ej. `rfc6749_0042`
- PDFs: `{doc_id}_{chunk_index:04d}` → ej. `pdf_my_report_0007`

El `chunk_index` es global por documento (no por sección), garantizando unicidad.

---

## Flujo de ingesta

### RFCs

```
fetch_rfc() → split_by_sections() → _subdivide() → embed() → collection.upsert()
```

- `split_by_sections`: regex `^(\d+(?:\.\d+)*\.?\s{2,}.+)$` detecta cabeceras numeradas
- El preámbulo (antes de la sección 1) se indexa como chunk `Preamble`
- Cada sub-chunk lleva el título de sección como prefijo para mejorar retrieval

### PDFs

```
_extract_text_from_pdf() → _split_pdf_into_chunks() → embed() → collection.upsert()
```

- `fitz.open(stream=...)` extrae texto por página
- Chunking por párrafos (`\n\n`): acumula hasta `CHUNK_SIZE * 4` caracteres, con overlap
- PDFs escaneados sin capa de texto lanzarán `ValueError`

---

## Endpoints de la API

### Públicos (sin autenticación)

| Método | Path | Rate limit | Descripción |
|--------|------|-----------|-------------|
| GET | `/` | — | Health check + stats |
| GET | `/rfcs` | — | Catálogo de RFCs disponibles |
| GET | `/rfcs/indexed` | — | RFCs ya indexados en ChromaDB |
| GET | `/documents` | — | PDFs subidos e indexados |
| POST | `/ask` | 5/min · 100/día por IP | Pregunta al RAG |

### Admin (requieren `X-API-Key` si `API_KEY` está seteada)

| Método | Path | Descripción |
|--------|------|-------------|
| POST | `/ingest` | Indexa RFCs del catálogo (background job) |
| GET | `/ingest/{job_id}` | Estado de un job de ingesta |
| POST | `/documents/upload` | Sube e indexa un PDF (máx. 50 MB) |
| DELETE | `/collection` | Resetea la colección ChromaDB |

---

## Chunking: parámetros clave (`config.py`)

| Constante | Default | Efecto |
|-----------|---------|--------|
| `CHUNK_SIZE` | 800 | Tokens aproximados por chunk (×4 para chars) |
| `CHUNK_OVERLAP` | 100 | Tokens de solapamiento entre chunks consecutivos |
| `TOP_K` | 5 | Chunks recuperados por query |

---

## Añadir RFCs al catálogo

Edita `src/config.py`, diccionario `RFCS`:

```python
"rfc7662": {
    "url":         "https://www.rfc-editor.org/rfc/rfc7662.txt",
    "title":       "OAuth 2.0 Token Introspection",
    "description": "Endpoint para inspeccionar tokens OAuth",
},
```

Luego indexa:
```bash
# CLI
python -m src.main ingest rfc7662

# API
curl -X POST /ingest -H "X-API-Key: ..." -d '{"rfc_ids": ["rfc7662"]}'
```

---

## Ramas y deploy

| Rama | Propósito |
|------|-----------|
| `main` | Producción. NaN Cloud hace autodeploy en cada push |
| `feature/pdf-upload` | Soporte de ingesta de PDFs propios |
| `claude/dreamy-mendel-r7w4K` | Rama de fixes iniciales (mergeada a main) |

NaN Cloud detecta el push a `main` y reconstruye la imagen Docker automáticamente.
El `DB_PATH` en el contenedor es `/app/data/chroma_db`. Si el contenedor se reinicia,
la DB se pierde (ChromaDB vive dentro del contenedor). Ver sección de persistencia
en el README para opciones.

---

## Persistencia de ChromaDB

ChromaDB vive dentro del contenedor Docker. Opciones para no perder los datos:

1. **Pre-indexar y commitear la DB** (recomendado para RFCs estáticos):
   ```bash
   python -m src.main ingest
   # Eliminar data/chroma_db/ del .gitignore y commitear
   git add data/chroma_db && git commit -m "chore: pre-indexed chromadb"
   ```
   El `COPY data/ ./data/` del Dockerfile la incluirá en la imagen.

2. **Re-indexar en cada arranque**: añadir lógica en el `lifespan` de `api.py`
   para detectar colección vacía y lanzar ingesta automática.

3. **Base de datos externa**: migrar a Qdrant Cloud (free tier) o cualquier
   vector DB gestionada. Requiere cambiar el cliente en `ingestion.py` y `query.py`.
