import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest


def _bge_available():
    import httpx
    from backend.src.rag.config import OLLAMA_URL, EMBED_MODEL
    try:
        r = httpx.post(f"{OLLAMA_URL}/api/embed",
                       json={"model": EMBED_MODEL, "input": "x"}, timeout=10)
        return r.status_code == 200 and r.json().get("embeddings")
    except Exception:
        return False


@pytest.mark.skipif(not _bge_available(), reason="bge-m3 endpoint unavailable")
def test_eval_recall_meets_floor(clean_tables):
    from backend.src.rag.seed.build_seed import build
    from backend.src.rag.ingest import ingest_path
    from backend.src.rag.eval.run_eval import evaluate

    build()  # (re)generate seed files
    seed_dir = os.path.join(os.path.dirname(__file__), "../../src/rag/seed")
    ingest_path(os.path.abspath(seed_dir), conn=clean_tables)

    result = evaluate(clean_tables, k=6)
    print(f"\nrecall@6 = {result['recall_at_k']:.3f}  n={result['n']}  misses={result['misses']}")
    assert result["n"] >= 20, "eval set must have >= 20 cases"
    # Floor set from the observed baseline; start at 0.8 and raise once measured.
    assert result["recall_at_k"] >= 0.8, f"recall too low: {result}"


# ── MRR observe-only (spec 2026-07-12-rag-reranker §3.5) ─────────────────────
import yaml


def _seed2(conn, rows):
    """(doc_id, text, vec) — bản local của _seed bên test_retrieve."""
    for doc_id, text, vec in rows:
        conn.execute("INSERT INTO rag_documents (doc_id, source_file, content_hash) "
                     "VALUES (%s,%s,%s)", (doc_id, f"{doc_id}.docx", doc_id))
        conn.execute(
            "INSERT INTO rag_chunks (doc_id, source_file, doc_title, section_path, "
            "chunk_index, token_count, chunk_text, embedding, ts_vector) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, to_tsvector('simple', %s))",
            (doc_id, f"{doc_id}.docx", "T", "A › B", 0, 5, text, vec, text))


def test_evaluate_reports_mrr(clean_tables, monkeypatch, tmp_path):
    from backend.src.rag import retrieve as r
    from backend.src.rag.eval import run_eval
    _seed2(clean_tables, [
        ("A", "Khách hàng hoàn hàng trong 30 ngày", [1.0] + [0.0] * 1023),
        ("B", "Quy trình bảo trì máy CNC", [1.0, 1.0] + [0.0] * 1022),
    ])
    monkeypatch.setattr(r, "embed_query", lambda q: [1.0] + [0.0] * 1023)
    cases = [
        {"q": "hoàn hàng", "expect_file": "A.docx"},   # hit rank 0 → rr = 1.0
        {"q": "hoàn hàng", "expect_file": "Z.docx"},   # miss → rr = 0.0
    ]
    p = tmp_path / "eval.yaml"
    p.write_text(yaml.safe_dump(cases, allow_unicode=True), encoding="utf-8")
    monkeypatch.setattr(run_eval, "EVAL_SET", str(p))
    res = run_eval.evaluate(clean_tables, k=6)
    assert res["n"] == 2
    assert res["recall_at_k"] == 0.5
    assert res["mrr"] == 0.5                            # (1.0 + 0.0) / 2
    assert res["misses"] == ["hoàn hàng"]
