import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.rag.types import Chunk
from backend.src.agents.synthesis import build_citations


def _chunk(**kw):
    d = dict(chunk_id=1, doc_id="d", source_file="C:/docs/policy.docx", doc_title="P",
             section_path="Chính sách hoàn hàng › Mục 1", page=1, sheet=None,
             row_range=None, text="t", dense_score=0.7, sparse_score=None,
             rrf_score=0.02, rank=0)
    d.update(kw)
    return Chunk(**d)


def test_build_citations_text_format():
    foot = build_citations([_chunk()])
    assert "📄 Nguồn:" in foot
    assert "Chính sách hoàn hàng › Mục 1 (policy.docx, tr.1)" in foot


def test_build_citations_omits_page_when_none():
    foot = build_citations([_chunk(page=None)])
    assert "(policy.docx)" in foot and "tr." not in foot


def test_build_citations_xlsx_format():
    foot = build_citations([_chunk(source_file="C:/docs/bang_gia.xlsx", section_path=None,
                                   page=None, sheet="Bảng giá", row_range="row 3")])
    assert "Bảng giá (bang_gia.xlsx, row 3)" in foot


def test_build_citations_dedupes_same_source_section():
    foot = build_citations([_chunk(chunk_id=1), _chunk(chunk_id=2)])
    assert foot.count("•") == 1


def test_build_citations_empty():
    assert build_citations([]) == ""


import pytest
from unittest.mock import AsyncMock, MagicMock
from backend.src.rag.types import RetrievalResult
from backend.src.agents import synthesis as syn
from backend.tests.conftest import make_mock_llm


def _result(chunks):
    return RetrievalResult(query="q", query_used="q", chunks=chunks,
                           top_score=(chunks[0].rrf_score if chunks else 0.0),
                           total_candidates=len(chunks), method="hybrid-rrf")


@pytest.mark.asyncio
async def test_synthesize_empty_returns_guard_without_llm():
    llm = MagicMock(); llm.ainvoke = AsyncMock()
    out = await syn.synthesize("q", _result([]), llm)
    assert out == syn.GUARD_MSG
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_synthesize_below_floor_no_sparse_returns_guard_without_llm():
    llm = MagicMock(); llm.ainvoke = AsyncMock()
    c = _chunk(dense_score=0.2, sparse_score=None)
    out = await syn.synthesize("q", _result([c]), llm)
    assert out == syn.GUARD_MSG
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_synthesize_sparse_only_passes_prefilter_and_calls_llm():
    llm = make_mock_llm("Trả lời từ tài liệu.")
    c = _chunk(dense_score=None, sparse_score=0.1)
    out = await syn.synthesize("q", _result([c]), llm)
    assert "Trả lời từ tài liệu." in out  # LLM was used (pre-filter passed via sparse hit)


@pytest.mark.asyncio
async def test_synthesize_sentinel_returns_guard():
    llm = make_mock_llm("KHÔNG_ĐỦ_THÔNG_TIN")
    c = _chunk(dense_score=0.7)
    out = await syn.synthesize("q", _result([c]), llm)
    assert out == syn.GUARD_MSG


@pytest.mark.asyncio
async def test_synthesize_happy_appends_footer():
    llm = make_mock_llm("Khách được hoàn trong 30 ngày.")
    c = _chunk(dense_score=0.7, section_path="Chính sách hoàn hàng › Mục 1",
               source_file="C:/docs/policy.docx", page=1)
    out = await syn.synthesize("q", _result([c]), llm)
    assert "Khách được hoàn trong 30 ngày." in out
    assert "📄 Nguồn:" in out
    assert "policy.docx, tr.1" in out
