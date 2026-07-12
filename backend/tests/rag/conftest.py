import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest

TEST_SCHEMA = "rag_test"


@pytest.fixture(scope="session")
def rag_conn():
    from backend.src.rag import db
    try:
        conn = db.connect(schema=TEST_SCHEMA)
    except Exception as e:
        pytest.skip(f"Postgres unreachable: {e}")
    conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    db.ensure_schema(conn, TEST_SCHEMA)
    yield conn
    conn.execute(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE")
    conn.close()


@pytest.fixture
def clean_tables(rag_conn):
    rag_conn.execute("TRUNCATE rag_chunks, rag_documents CASCADE")
    yield rag_conn


@pytest.fixture(autouse=True)
def _rerank_off(monkeypatch):
    """Mặc định TẮT reranker trong mọi test RAG — sau khi retrieve() nối vào
    score_pairs thật, test cũ gọi retrieve() sẽ kích hoạt tải model 2.3GB từ
    HuggingFace giữa pytest (máy có mạng — tải THẬT). Test rerank bật lại
    bằng cách patch reranker.score_pairs trực tiếp (fake bỏ qua env) hoặc
    setenv RAG_RERANK_ENABLED=1."""
    monkeypatch.setenv("RAG_RERANK_ENABLED", "0")
