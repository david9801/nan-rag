#!/bin/bash
set -e

echo "📦 Instalando dependencias..."
pip install --break-system-packages \
  openai \
  chromadb \
  requests \
  rich \
  python-dotenv \
  fastapi \
  "uvicorn[standard]"

echo "✅ Listo"
