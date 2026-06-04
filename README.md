# RFC RAG — NaN Builders

Sistema de Retrieval-Augmented Generation (RAG) sobre estándares técnicos de Internet
(RFCs) y documentos PDF propios, con interfaz de chat web y API HTTP, desplegado en NaN Cloud.

**Stack:** `qwen3-embedding` + `deepseek-v4-flash` (NaN Builders) · ChromaDB · FastAPI · Alpine.js

---

## Inicio rápido

### Opción A — Usar la API directamente

```bash
# Preguntar al RAG (sin autenticación)
curl -X POST https://<tu-url>/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "¿Qué es PKCE y por qué es necesario?"}'

# Filtrar por RFC concreto
curl -X POST https://<tu-url>/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "¿Cómo funciona el Authorization Code Grant?", "rfc_filter": "rfc6749"}'
```

### Opción B — Interfaz de chat web

Abre en el navegador: `https://<tu-url>/ui`

- Typewriter effect en respuestas
- Acordeón de fuentes con score de relevancia
- Selector de RFC en el sidebar
- Responsive (móvil y escritorio)

### Opción C — Local

```bash
git clone https://github.com/david9801/nan-rag
cd nan-rag

cp .env.example .env
# Editar .env y añadir NAN_API_KEY

bash setup.sh

# CLI
python -m src.main ingest rfc6749 rfc7519 rfc7636
python -m src.main ask "¿Qué es un Bearer token?"

# API + UI local
uvicorn src.api:app --port 3000 --reload
# Abre http://localhost:3000/ui
```

---

## RFCs incluidos en el catálogo

| ID | Título |
|----|--------|
| `rfc6749` | OAuth 2.0 Authorization Framework |
| `rfc6750` | OAuth 2.0 Bearer Token Usage |
| `rfc7519` | JSON Web Token (JWT) |
| `rfc7636` | PKCE for OAuth Public Clients |
| `rfc8414` | OAuth 2.0 Authorization Server Metadata |
| `rfc8446` | TLS 1.3 |
| `rfc9110` | HTTP Semantics |

Para añadir más RFCs edita el diccionario `RFCS` en `src/config.py`.

---

## API — Referencia rápida

### Endpoints públicos

#### `GET /`
Health check.
```json
{"status": "ok", "chunks_indexed": 742, "embed_model": "qwen3-embedding", "llm_model": "deepseek-v4-flash"}
```

#### `GET /ui`
Interfaz de chat web (Alpine.js + Marked.js + Tailwind, sin build step).

#### `GET /rfcs`
Catálogo completo de RFCs disponibles para indexar.

#### `GET /rfcs/indexed`
RFCs ya indexados en ChromaDB.
```json
{"indexed": ["rfc6749", "rfc7519"], "total_chunks": 196}
```

#### `GET /documents`
PDFs subidos manualmente e indexados.
```json
{"documents": [{"doc_id": "pdf_my_report", "filename": "my_report.pdf", "title": "My Report"}]}
```

#### `POST /ask`
Pregunta al RAG. Límite: **5 peticiones/minuto y 100/día por IP**.

```bash
curl -X POST /ask \
  -H "Content-Type: application/json" \
  -d '{
    "question": "¿Cómo previene PKCE el ataque de interceptación?",
    "rfc_filter": "rfc7636",
    "stream": false
  }'
```

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `question` | string | Pregunta en cualquier idioma |
| `rfc_filter` | string \| null | Limitar búsqueda a un documento por su ID (RFC o PDF) |
| `stream` | bool | `true` para respuesta en streaming (`text/plain`) |

Respuesta:
```json
{
  "answer": "PKCE previene el ataque porque...",
  "sources": [
    {"rfc_id": "rfc7636", "title": "PKCE for OAuth Public Clients",
     "section_title": "1.1.  Protocol Flow", "score": 0.74}
  ]
}
```

---

### Endpoints admin (requieren `X-API-Key` header)

#### `POST /ingest`
Indexa RFCs del catálogo en background. Devuelve un `job_id`.

```bash
curl -X POST /ingest \
  -H "X-API-Key: <clave>" \
  -H "Content-Type: application/json" \
  -d '{"rfc_ids": ["rfc6749", "rfc7519"]}'
  # rfc_ids vacío = indexar todos
```

#### `GET /ingest/{job_id}`
Estado del job: `running` · `done` · `error`.

#### `POST /documents/upload`
Sube e indexa un PDF propio. Máximo **50 MB**. Solo acepta `.pdf`.

```bash
curl -X POST /documents/upload \
  -H "X-API-Key: <clave>" \
  -F "file=@mi_documento.pdf" \
  -F "title=Mi Documento Técnico"      # opcional
```

Respuesta:
```json
{
  "status": "ok",
  "doc_id": "pdf_mi_documento",
  "filename": "mi_documento.pdf",
  "title": "Mi Documento Técnico",
  "pages": 42,
  "chunks": 87,
  "hint": "Usa rfc_filter: \"pdf_mi_documento\" en POST /ask para buscar solo en este documento"
}
```

Después de subir, filtra la búsqueda al documento:
```bash
curl -X POST /ask \
  -H "Content-Type: application/json" \
  -d '{"question": "¿Cuál es la conclusión del capítulo 3?", "rfc_filter": "pdf_mi_documento"}'
```

#### `DELETE /collection`
Borra y recrea la colección ChromaDB. Útil para resetear tras un estado corrupto.

---

## Deploy en NaN Cloud

1. Fork o usa el repositorio directamente.
2. En NaN Cloud → Apps → New App:
   - Repository: `david9801/nan-rag`
   - Branch: `main`
   - Dockerfile path: `Dockerfile`
   - Container port: `3000`
3. Variables de entorno (Runtime):
   - `NAN_API_KEY` — **obligatoria**
   - `API_KEY` — recomendada en producción (protege endpoints admin)
   - `DB_PATH` — `/app/data/chroma_db` (ya incluida en el Dockerfile como default)
4. Deploy → esperar build (~2 min).
5. Indexar RFCs:
   ```bash
   curl -X POST https://<tu-url>/ingest \
     -H "X-API-Key: <API_KEY>" \
     -d '{"rfc_ids": ["rfc6749", "rfc7519", "rfc7636"]}'
   ```
6. Abrir la UI: `https://<tu-url>/ui`

> **Nota sobre persistencia:** ChromaDB vive dentro del contenedor. Al reiniciar se
> pierden los datos indexados. Solución recomendada: tras indexar, commitear el
> directorio `data/chroma_db/` al repositorio (elimínalo del `.gitignore`) y el
> `COPY data/ ./data/` del Dockerfile lo incluirá en la imagen.

---

## Tests

```bash
pip install -r requirements-dev.txt
pytest tests/ -v
```

36 tests (20 de API + 16 de ingestion), sin credenciales ni red real. ChromaDB y OpenAI mockeados.

---

## Estructura del proyecto

```
nan-rag/
├── Dockerfile
├── requirements.txt
├── requirements-dev.txt          # dependencias de test (pytest, httpx, pymupdf...)
├── setup.sh                      # instalación local rápida
├── .env.example                  # plantilla de configuración
├── CLAUDE.md                     # documentación técnica para desarrolladores
├── frontend/
│   └── index.html                # chat UI (Alpine.js + Marked.js + Tailwind CDN)
├── data/
│   └── chroma_db/                # ChromaDB persistente (excluida del repo)
├── tests/
│   ├── conftest.py               # fixtures con mocks de ChromaDB y OpenAI
│   ├── test_api.py               # tests de endpoints HTTP
│   └── test_ingestion.py         # tests de chunking e ingesta
└── src/
    ├── __init__.py
    ├── config.py                 # constantes, cliente NaN, catálogo de RFCs
    ├── ingestion.py              # descarga RFCs, parsea PDFs, chunking, embedding
    ├── query.py                  # retrieval por similitud coseno + generación LLM
    ├── api.py                    # FastAPI: todos los endpoints HTTP + StaticFiles
    └── main.py                   # CLI: ingest / ask / list
```
