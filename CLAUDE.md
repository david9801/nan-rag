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
| Frontend | Alpine.js + Marked.js + Tailwind CDN (servido por FastAPI en `/ui`) |
| Tests | pytest + httpx, mocks de ChromaDB y OpenAI |
| Deploy | Docker en NaN Cloud, linked a la rama `main` de GitHub |

---

## Estructura del proyecto

```
nan-rag/
├── Dockerfile
├── requirements.txt              # dependencias de producción
├── requirements-dev.txt          # dependencias de test
├── setup.sh                      # instalación rápida sin Docker
├── .env.example                  # plantilla de variables de entorno
├── frontend/
│   └── index.html                # chat UI (sin build step, todo CDN)
├── data/
│   └── chroma_db/                # ChromaDB persistente (excluida del repo por .gitignore)
├── tests/
│   ├── conftest.py               # fixtures: ChromaDB y OpenAI mockeados
│   ├── test_api.py               # 20 tests de endpoints HTTP
│   └── test_ingestion.py         # 16 tests de chunking e ingesta
└── src/
    ├── __init__.py               # convierte src/ en paquete Python
    ├── config.py                 # constantes, cliente OpenAI, catálogo RFCS, DB_PATH
    ├── ingestion.py              # fetch RFCs, parsea PDFs, chunking, embedding, upsert
    ├── query.py                  # retrieve (embed query + búsqueda coseno) + generación LLM
    ├── api.py                    # FastAPI: endpoints HTTP, auth, rate limiting, StaticFiles
    └── main.py                   # CLI: ingest / ask / list
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
# Instalar dependencias de producción
bash setup.sh

# Instalar dependencias de test
pip install -r requirements-dev.txt

# Configurar entorno
cp .env.example .env
# Editar .env con NAN_API_KEY

# CLI
python -m src.main list
python -m src.main ingest rfc6749 rfc7519
python -m src.main ask "¿Qué es PKCE?"

# API + UI local
uvicorn src.api:app --host 0.0.0.0 --port 3000 --reload
# UI disponible en http://localhost:3000/ui

# Tests
pytest tests/ -v
```

---

## Frontend (`frontend/index.html`)

Fichero HTML autocontenido servido por FastAPI vía `StaticFiles` en `/ui`.
Sin npm, sin bundler, sin build step.

| Librería | Versión CDN | Para qué |
|----------|-------------|---------|
| Alpine.js | 3.x | Reactividad y estado del chat |
| Marked.js | latest | Renderizar Markdown del LLM |
| Tailwind CSS | CDN | Estilos |
| Highlight.js | 11.9 | Syntax highlighting en bloques de código |

### Comportamiento del chat

- Al cargar: llama a `GET /`, `GET /rfcs/indexed` y `GET /rfcs` para poblar el sidebar y las stats
- Al enviar pregunta: llama a `POST /ask` con `stream: false` y anima la respuesta con efecto typewriter (4 chars / 8 ms)
- Fuentes: acordeón colapsado por defecto, score coloreado (verde > 0.7, amarillo > 0.5)
- Rate limit (429): muestra mensaje claro en la burbuja de error
- El mount es condicional en `api.py`: si `frontend/` no existe, la API sigue funcionando

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

El `chunk_index` es global por documento (renumerado en `ingest()` tras `split_by_sections()`),
garantizando unicidad en el `upsert`.

---

## Flujo de ingesta

### RFCs

```
fetch_rfc() → split_by_sections() → _subdivide() → renumerar global → embed() → collection.upsert()
```

- `split_by_sections`: regex `^(\d+(?:\.\d+)*\.?\s{2,}.+)$` detecta cabeceras numeradas
- El preámbulo (antes de la sección 1) se indexa como chunk `Preamble`
- Si no hay secciones numeradas, todo el texto se indexa como `Preamble`
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
| GET | `/ui` | — | Interfaz de chat web |
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

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

| Fichero | Tests | Qué cubre |
|---------|-------|-----------|
| `tests/conftest.py` | — | Fixtures: colección mock, TestClient con ChromaDB y OpenAI mockeados |
| `tests/test_api.py` | 20 | health, /rfcs, /rfcs/indexed, /ask (ok, vacío, sin resultados, 422), /ingest (auth, IDs inválidos, 202), /ingest/{job_id}, /ui, DELETE /collection |
| `tests/test_ingestion.py` | 16 | split_by_sections (preamble, secciones, prefijo, texto vacío, sin secciones), _subdivide (overlap, longitud), ingest (upsert, IDs únicos, metadata) |

Los tests no requieren credenciales ni red: `NAN_API_KEY` se fija a `"test-key"` en conftest,
ChromaDB y el cliente OpenAI se mockean con `unittest.mock.patch`.

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
| `feature/pdf-upload` | Soporte de ingesta de PDFs propios (PR #2) |
| `feature/frontend` | Chat UI + tests (PR #3) |
| `claude/dreamy-mendel-r7w4K` | Fixes iniciales (mergeada a main) |

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
