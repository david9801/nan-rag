FROM python:3.12-slim

WORKDIR /app

# Dependencias primero (capa cacheada)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código fuente
COPY src/ ./src/

# Directorio persistente para ChromaDB
RUN mkdir -p /app/data/chroma_db

EXPOSE 3000

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "3000"]
