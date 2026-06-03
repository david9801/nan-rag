FROM python:3.12-slim

WORKDIR /app

# Dependencias primero (capa cacheada)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código fuente
COPY src/ ./src/

# DB pre-indexada (si existe en el repo se copia; si no, se crea vacía)
RUN mkdir -p /app/data/chroma_db
COPY data/ ./data/

EXPOSE 3000

# DB_PATH se puede sobreescribir con -e DB_PATH=...
ENV DB_PATH=/app/data/chroma_db

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "3000"]
