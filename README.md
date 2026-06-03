# RFC RAG — NaN Builders

RAG sobre estándares RFC usando el cluster de inferencia de NaN.

**Stack:**
- Embeddings: `qwen3-embedding` (NaN cluster)
- LLM: `deepseek-v4-flash` (NaN cluster)
- Vector DB: ChromaDB local persistente

---

## Setup en la microVM

```bash
# 1. Clonar/subir el proyecto a la microVM
cd rfc-rag

# 2. Instalar dependencias
bash setup.sh

# 3. Configurar API key de NaN
cp .env.example .env
nano .env   # pega tu NAN_API_KEY

# 4. Ir al directorio de código
cd src
```

## Uso

```bash
# Ver RFCs disponibles en el catálogo
python main.py list

# Indexar RFCs específicos (recomendado para empezar)
python main.py ingest rfc6749 rfc7519 rfc7636

# Indexar todos los del catálogo
python main.py ingest

# Modo interactivo
python main.py ask

# Pregunta directa
python main.py ask "¿Qué es el Authorization Code Grant?"

# Filtrar por RFC específico
python main.py ask "@rfc6749 ¿Cuánto puede durar un access token?"
```

## Añadir más RFCs

Edita `src/config.py` y añade una entrada al diccionario `RFCS`:

```python
"rfc7662": {
    "url":         "https://www.rfc-editor.org/rfc/rfc7662.txt",
    "title":       "OAuth 2.0 Token Introspection",
    "description": "Endpoint para inspeccionar tokens OAuth",
},
```

Luego indexa el nuevo RFC:
```bash
python main.py ingest rfc7662
```

## Estructura

```
rfc-rag/
├── setup.sh          # instala dependencias
├── .env.example      # plantilla de configuración
├── data/
│   ├── rfcs/         # caché de RFCs descargados (opcional)
│   └── chroma_db/    # base de datos vectorial persistente
└── src/
    ├── config.py     # configuración, cliente NaN, catálogo de RFCs
    ├── ingestion.py  # descarga, chunking y embedding
    ├── query.py      # recuperación y generación de respuestas
    └── main.py       # CLI principal
```
