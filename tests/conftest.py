"""
Fixtures compartidas para todos los tests.
Mockea ChromaDB y el cliente OpenAI para no requerir credenciales ni red.
"""
import os
import pytest
from unittest.mock import MagicMock, patch

# Establecer credenciales dummy ANTES de cualquier import del paquete src,
# para que config.py pueda construir el cliente OpenAI sin fallar.
os.environ.setdefault("NAN_API_KEY", "test-key")


def make_mock_collection(count: int = 10):
    """Devuelve un mock de chromadb.Collection con comportamiento básico."""
    col = MagicMock()
    col.count.return_value = count
    col.get.return_value = {
        "metadatas": [
            {"rfc_id": "rfc6749", "title": "OAuth 2.0", "section_title": "1. Introduction"},
            {"rfc_id": "rfc7519", "title": "JWT",        "section_title": "Preamble"},
        ]
    }
    col.query.return_value = {
        "documents": [["Texto de prueba sobre OAuth."]],
        "metadatas": [[{"rfc_id": "rfc6749", "title": "OAuth 2.0", "section_title": "1. Introduction"}]],
        "distances": [[0.25]],
    }
    return col


@pytest.fixture()
def mock_collection():
    return make_mock_collection()


@pytest.fixture()
def empty_collection():
    return make_mock_collection(count=0)


@pytest.fixture()
def client_app(mock_collection):
    """TestClient con ChromaDB y OpenAI mockeados."""
    with (
        patch("src.api.chromadb.PersistentClient") as mock_db,
        patch("src.query.client") as mock_query_openai,
    ):
        mock_db.return_value.get_or_create_collection.return_value = mock_collection

        # Mock de embeddings
        mock_embed_resp = MagicMock()
        mock_embed_resp.data = [MagicMock(embedding=[0.1] * 4096)]
        mock_query_openai.embeddings.create.return_value = mock_embed_resp

        # Mock de completions
        mock_completion = MagicMock()
        mock_completion.choices[0].message.content = "Respuesta de prueba."
        mock_query_openai.chat.completions.create.return_value = mock_completion

        from src.api import app
        import src.api as api_module
        # Parchear también src.api.client (nombre importado de src.query)
        with patch("src.api.client") as mock_api_openai:
            mock_api_openai.chat.completions.create.return_value = mock_completion
            api_module._collection = mock_collection
            from fastapi.testclient import TestClient
            yield TestClient(app)


@pytest.fixture()
def client_app_empty(empty_collection):
    """TestClient con la colección vacía."""
    with patch("src.api.chromadb.PersistentClient") as mock_db:
        mock_db.return_value.get_or_create_collection.return_value = empty_collection
        import src.api as api_module
        api_module._collection = empty_collection
        from src.api import app
        from fastapi.testclient import TestClient
        yield TestClient(app)
