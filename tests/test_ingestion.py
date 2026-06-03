"""Tests de las funciones de chunking e ingesta (sin red ni ChromaDB)."""
import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("NAN_API_KEY", "test-key")


# ── split_by_sections ─────────────────────────────────────────────────────────

def make_rfc_text():
    return """\
Network Working Group                                          D. Hardt
Request for Comments: 6749                                   Microsoft
Category: Standards Track                                 October 2012

Abstract

   The OAuth 2.0 authorization framework enables a third-party
   application to obtain limited access to an HTTP service.

1.  Introduction

   In the traditional client-server authentication model, the client
   requests an access-restricted resource on the server.

1.1.  Roles

   OAuth defines four roles: resource owner, resource server,
   client, and authorization server.

2.  Client Registration

   Before initiating the protocol, the client registers with the
   authorization server.
"""


def test_split_by_sections_returns_chunks():
    from src.ingestion import split_by_sections
    chunks = split_by_sections(make_rfc_text(), "rfc6749")
    assert len(chunks) > 0


def test_split_by_sections_includes_preamble():
    from src.ingestion import split_by_sections
    chunks = split_by_sections(make_rfc_text(), "rfc6749")
    titles = [c["section_title"] for c in chunks]
    assert "Preamble" in titles


def test_split_by_sections_detects_numbered_sections():
    from src.ingestion import split_by_sections
    chunks = split_by_sections(make_rfc_text(), "rfc6749")
    titles = [c["section_title"] for c in chunks]
    assert any("Introduction" in t for t in titles)


def test_split_by_sections_rfc_id_set_correctly():
    from src.ingestion import split_by_sections
    chunks = split_by_sections(make_rfc_text(), "rfc6749")
    assert all(c["rfc_id"] == "rfc6749" for c in chunks)


def test_split_by_sections_chunk_index_unique_after_renumber():
    """ingest() renumera globalmente; verificamos que los IDs resultantes son únicos."""
    from src.ingestion import split_by_sections
    chunks = split_by_sections(make_rfc_text(), "rfc6749")
    for i, chunk in enumerate(chunks):
        chunk["chunk_index"] = i
    ids = [f"rfc6749_{c['chunk_index']:04d}" for c in chunks]
    assert len(ids) == len(set(ids))


def test_split_by_sections_prefix_in_text():
    from src.ingestion import split_by_sections
    chunks = split_by_sections(make_rfc_text(), "rfc6749")
    # Cada chunk debe tener el título de sección como prefijo
    for c in chunks:
        assert c["section_title"] in c["text"]


def test_split_empty_text_returns_empty():
    from src.ingestion import split_by_sections
    chunks = split_by_sections("   ", "rfc6749")
    assert chunks == []


def test_split_text_without_sections_returns_preamble_only():
    from src.ingestion import split_by_sections
    text = "This is a document without numbered sections.\n" * 5
    chunks = split_by_sections(text, "rfc6749")
    # Sin secciones numeradas, todo va al preamble
    assert len(chunks) >= 1
    assert all(c["section_title"] == "Preamble" for c in chunks)


# ── _subdivide ────────────────────────────────────────────────────────────────

def test_subdivide_short_text_is_one_chunk():
    from src.ingestion import _subdivide
    chunks = _subdivide("Short text.", "rfc6749", "1. Intro")
    assert len(chunks) == 1


def test_subdivide_long_text_creates_multiple_chunks():
    from src.ingestion import _subdivide
    long_text = "word " * 2000   # ~10 000 chars >> CHUNK_SIZE*4=3200
    chunks = _subdivide(long_text, "rfc6749", "1. Intro")
    assert len(chunks) > 1


def test_subdivide_chunks_have_overlap():
    from src.ingestion import _subdivide, CHUNK_SIZE, CHUNK_OVERLAP
    # Con overlap los chunks consecutivos comparten contenido
    long_text = "A" * (CHUNK_SIZE * 4 * 2)
    chunks = _subdivide(long_text, "rfc6749", "Sec")
    assert len(chunks) >= 2


def test_subdivide_section_title_is_prefixed():
    from src.ingestion import _subdivide
    chunks = _subdivide("Some content here.", "rfc6749", "3.1.  My Section")
    assert "3.1.  My Section" in chunks[0]["text"]


def test_subdivide_all_chunks_have_correct_rfc_id():
    from src.ingestion import _subdivide
    long_text = "X " * 1000
    chunks = _subdivide(long_text, "rfc7519", "1. Intro")
    assert all(c["rfc_id"] == "rfc7519" for c in chunks)


# ── ingest (integración con mocks) ───────────────────────────────────────────

def test_ingest_calls_upsert(mock_collection):
    with (
        patch("src.ingestion.fetch_rfc", return_value=make_rfc_text()),
        patch("src.ingestion.embed", return_value=[[0.1] * 4096] * 32),
    ):
        from src.ingestion import ingest
        ingest(["rfc6749"], mock_collection)
        assert mock_collection.upsert.called


def test_ingest_chunk_ids_are_unique(mock_collection):
    """Verifica que no se generan IDs duplicados en el upsert."""
    ids_seen = []

    def capture_upsert(**kwargs):
        ids_seen.extend(kwargs.get("ids", []))

    mock_collection.upsert.side_effect = capture_upsert

    with (
        patch("src.ingestion.fetch_rfc", return_value=make_rfc_text()),
        patch("src.ingestion.embed", return_value=[[0.1] * 4096] * 32),
    ):
        from src.ingestion import ingest
        ingest(["rfc6749"], mock_collection)

    assert len(ids_seen) == len(set(ids_seen)), "IDs duplicados detectados en upsert"


def test_ingest_metadata_has_required_fields(mock_collection):
    metadatas_seen = []

    def capture_upsert(**kwargs):
        metadatas_seen.extend(kwargs.get("metadatas", []))

    mock_collection.upsert.side_effect = capture_upsert

    with (
        patch("src.ingestion.fetch_rfc", return_value=make_rfc_text()),
        patch("src.ingestion.embed", return_value=[[0.1] * 4096] * 32),
    ):
        from src.ingestion import ingest
        ingest(["rfc6749"], mock_collection)

    assert len(metadatas_seen) > 0
    for m in metadatas_seen:
        assert "rfc_id" in m
        assert "section_title" in m
        assert "title" in m
