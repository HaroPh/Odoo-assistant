import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest


def _seed(conn, rows):
    """rows: list of (doc_id, text, vec). Inserts a doc + one chunk each."""
    for doc_id, text, vec in rows:
        conn.execute("INSERT INTO rag_documents (doc_id, source_file, content_hash) "
                     "VALUES (%s,%s,%s)", (doc_id, f"{doc_id}.docx", doc_id))
        conn.execute(
            "INSERT INTO rag_chunks (doc_id, source_file, doc_title, section_path, "
            "chunk_index, token_count, chunk_text, embedding, ts_vector) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, to_tsvector('simple', %s))",
            (doc_id, f"{doc_id}.docx", "T", "A › B", 0, 5, text, vec, text))


def test_retrieve_returns_result_with_scores_and_ordering(clean_tables, monkeypatch):
    from backend.src.rag import retrieve as r
    # doc A is the exact dense match; doc B is far
    _seed(clean_tables, [
        ("A", "Khách hàng hoàn hàng trong 30 ngày", [1.0] + [0.0] * 1023),
        ("B", "Quy trình bảo trì máy CNC", [0.0] * 1023 + [1.0]),
    ])
    monkeypatch.setattr(r, "embed_query", lambda q: [1.0] + [0.0] * 1023)

    res = r.retrieve("chính sách hoàn hàng", k=5, conn=clean_tables)
    assert res.method == "hybrid-rrf"
    assert not res.is_empty()
    assert res.chunks[0].doc_id == "A"                 # nearest dense → top
    assert res.top_score == res.chunks[0].rrf_score
    assert res.chunks[0].rank == 0
    assert res.chunks[0].dense_score is not None


def test_retrieve_empty_on_no_match(clean_tables, monkeypatch):
    from backend.src.rag import retrieve as r
    monkeypatch.setattr(r, "embed_query", lambda q: [1.0] + [0.0] * 1023)
    res = r.retrieve("không có gì", k=5, conn=clean_tables)
    assert res.is_empty() and res.top_score == 0.0
