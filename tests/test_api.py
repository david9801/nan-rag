"""Tests de los endpoints HTTP de la API."""
import pytest
from unittest.mock import MagicMock, patch


# ── GET / ─────────────────────────────────────────────────────────────────────

def test_health_ok(client_app):
    resp = client_app.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "chunks_indexed" in data
    assert "embed_model" in data
    assert "llm_model" in data


def test_health_returns_chunk_count(client_app, mock_collection):
    mock_collection.count.return_value = 42
    resp = client_app.get("/")
    assert resp.json()["chunks_indexed"] == 42


# ── GET /rfcs ─────────────────────────────────────────────────────────────────

def test_list_rfcs_returns_catalog(client_app):
    resp = client_app.get("/rfcs")
    assert resp.status_code == 200
    data = resp.json()
    assert "rfc6749" in data
    assert "title" in data["rfc6749"]
    assert "url" in data["rfc6749"]
    assert "description" in data["rfc6749"]


def test_list_rfcs_contains_all_entries(client_app):
    resp = client_app.get("/rfcs")
    data = resp.json()
    expected = {"rfc6749", "rfc6750", "rfc7636", "rfc8414", "rfc7519", "rfc9110", "rfc8446"}
    assert expected.issubset(set(data.keys()))


# ── GET /rfcs/indexed ─────────────────────────────────────────────────────────

def test_indexed_rfcs_empty_collection(client_app_empty):
    resp = client_app_empty.get("/rfcs/indexed")
    assert resp.status_code == 200
    data = resp.json()
    assert data["indexed"] == []
    assert data["total_chunks"] == 0


def test_indexed_rfcs_with_data(client_app, mock_collection):
    mock_collection.count.return_value = 5
    resp = client_app.get("/rfcs/indexed")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["indexed"], list)
    assert "rfc6749" in data["indexed"]
    assert data["total_chunks"] == 5


# ── POST /ask ─────────────────────────────────────────────────────────────────

def test_ask_returns_answer_and_sources(client_app):
    resp = client_app.post("/ask", json={"question": "¿Qué es OAuth?"})
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert "sources" in data
    assert isinstance(data["sources"], list)


def test_ask_source_has_expected_fields(client_app):
    resp = client_app.post("/ask", json={"question": "¿Qué es OAuth?"})
    sources = resp.json()["sources"]
    assert len(sources) > 0
    src = sources[0]
    assert "rfc_id" in src
    assert "title" in src
    assert "section_title" in src
    assert "score" in src


def test_ask_with_rfc_filter(client_app):
    resp = client_app.post("/ask", json={"question": "Bearer tokens", "rfc_filter": "rfc6749"})
    assert resp.status_code == 200


def test_ask_empty_db_returns_400(client_app_empty):
    resp = client_app_empty.post("/ask", json={"question": "¿Qué es OAuth?"})
    assert resp.status_code == 400
    assert "vacía" in resp.json()["detail"]


def test_ask_no_results_returns_404(client_app, mock_collection):
    mock_collection.query.return_value = {
        "documents": [[]],
        "metadatas": [[]],
        "distances": [[]],
    }
    resp = client_app.post("/ask", json={"question": "pregunta sin resultado"})
    assert resp.status_code == 404


def test_ask_missing_question_returns_422(client_app):
    resp = client_app.post("/ask", json={})
    assert resp.status_code == 422


# ── POST /ingest ──────────────────────────────────────────────────────────────

def test_ingest_requires_auth(client_app):
    import src.api as api_module
    original = api_module.API_KEY

    with patch.object(api_module, "API_KEY", "secret"):
        resp = client_app.post("/ingest", json={"rfc_ids": ["rfc6749"]})
        assert resp.status_code == 403

    api_module.API_KEY = original


def test_ingest_unknown_rfc_returns_400(client_app):
    resp = client_app.post("/ingest", json={"rfc_ids": ["rfc9999"]})
    # Sin API_KEY configurada, pasa la auth; valida los IDs
    assert resp.status_code == 400
    assert "no reconocidos" in resp.json()["detail"]


def test_ingest_valid_returns_202(client_app):
    with patch("src.api.ingest"):
        resp = client_app.post("/ingest", json={"rfc_ids": ["rfc6749"]})
    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert "job_id" in data


# ── GET /ingest/{job_id} ──────────────────────────────────────────────────────

def test_ingest_status_unknown_job(client_app):
    resp = client_app.get("/ingest/non-existent-id")
    assert resp.status_code == 404


def test_ingest_status_known_job(client_app):
    with patch("src.api.ingest"):
        post_resp = client_app.post("/ingest", json={"rfc_ids": ["rfc6749"]})
    job_id = post_resp.json()["job_id"]

    resp = client_app.get(f"/ingest/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] in ("running", "done", "error")


# ── GET /ui (frontend) ────────────────────────────────────────────────────────

def test_frontend_served(client_app):
    resp = client_app.get("/ui/")
    # 200 si el directorio frontend/ existe, 404 si no (entorno de test sin ficheros)
    assert resp.status_code in (200, 404)


# ── DELETE /collection ────────────────────────────────────────────────────────

def test_reset_collection_no_auth(client_app):
    # Sin API_KEY configurada debe pasar
    resp = client_app.request("DELETE", "/collection")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_reset_collection_wrong_key(client_app):
    import src.api as api_module
    with patch.object(api_module, "API_KEY", "secret"):
        resp = client_app.request("DELETE", "/collection", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 403
