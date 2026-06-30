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
