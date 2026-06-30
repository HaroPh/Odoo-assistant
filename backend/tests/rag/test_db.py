import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))


def test_ensure_schema_is_idempotent(rag_conn):
    from backend.src.rag import db
    # second call must not raise
    db.ensure_schema(rag_conn, "rag_test")
    rows = rag_conn.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'rag_test' ORDER BY table_name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert {"rag_documents", "rag_chunks"} <= names


def test_indexes_exist(rag_conn):
    rows = rag_conn.execute(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'rag_test'"
    ).fetchall()
    idx = " ".join(r[0] for r in rows)
    assert "embedding" in idx  # HNSW index present
    assert "ts" in idx         # GIN ts_vector index present
