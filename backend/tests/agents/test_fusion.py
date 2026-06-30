# backend/tests/agents/test_fusion.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
from backend.src.rag.types import Chunk, RetrievalResult


def _chunk(**kw):
    d = dict(chunk_id=1, doc_id="d", source_file="C:/docs/policy.docx", doc_title="P",
             section_path="Chính sách hoàn hàng › Điều 4", page=1, sheet=None,
             row_range=None, text="Hoàn hàng trong 30 ngày.", dense_score=0.7,
             sparse_score=None, rrf_score=0.02, rank=0)
    d.update(kw)
    return Chunk(**d)


def _result(chunks):
    return RetrievalResult(query="q", query_used="q", chunks=chunks,
                           top_score=(chunks[0].rrf_score if chunks else 0.0),
                           total_candidates=len(chunks), method="hybrid-rrf")


@pytest.mark.asyncio
async def test_search_documents_empty_returns_sentinel_no_collect(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    monkeypatch.setattr(fusion_mod, "retrieve", lambda q, *a, **kw: _result([]))
    collected = []
    tool = fusion_mod._make_search_documents_tool(collected)
    out = await tool.ainvoke({"query": "thủ đô nước Pháp?"})
    assert out == "Không tìm thấy tài liệu liên quan."
    assert collected == []


@pytest.mark.asyncio
async def test_search_documents_below_floor_returns_sentinel(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    c = _chunk(dense_score=0.2, sparse_score=None)
    monkeypatch.setattr(fusion_mod, "retrieve", lambda q, *a, **kw: _result([c]))
    collected = []
    tool = fusion_mod._make_search_documents_tool(collected)
    out = await tool.ainvoke({"query": "câu ngoài corpus"})
    assert out == "Không tìm thấy tài liệu liên quan."
    assert collected == []


@pytest.mark.asyncio
async def test_search_documents_passing_returns_text_and_collects(monkeypatch):
    import backend.src.agents.fusion as fusion_mod
    c = _chunk(dense_score=0.7)
    monkeypatch.setattr(fusion_mod, "retrieve", lambda q, *a, **kw: _result([c]))
    collected = []
    tool = fusion_mod._make_search_documents_tool(collected)
    out = await tool.ainvoke({"query": "chính sách hoàn hàng"})
    assert "Hoàn hàng trong 30 ngày." in out
    assert len(collected) == 1
    assert collected[0].section_path == "Chính sách hoàn hàng › Điều 4"
