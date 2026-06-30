import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.rag.types import Chunk, RetrievalResult


def _chunk(rank=0, score=1.0):
    return Chunk(chunk_id=rank, doc_id="d1", source_file="f.pdf", doc_title="F",
                 section_path="A › B", page=1, sheet=None, row_range=None,
                 text="t", dense_score=0.9, sparse_score=0.1, rrf_score=score, rank=rank)


def test_retrieval_result_is_empty_and_top_score():
    empty = RetrievalResult(query="q", query_used="q", chunks=[], top_score=0.0,
                            total_candidates=0, method="hybrid-rrf")
    assert empty.is_empty() is True

    c = _chunk(rank=0, score=0.42)
    full = RetrievalResult(query="q", query_used="q", chunks=[c], top_score=0.42,
                           total_candidates=3, method="hybrid-rrf")
    assert full.is_empty() is False
    assert full.top_score == full.chunks[0].rrf_score


def test_chunk_is_frozen():
    import dataclasses, pytest
    c = _chunk()
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.text = "x"
