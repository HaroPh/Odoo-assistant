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


from backend.src.agents.synthesis import extract_used_citations, USED_MARKER


def _two_chunks():
    c1 = _chunk(chunk_id=1, source_file="C:/docs/policy.docx",
                section_path="Chính sách hoàn hàng › Mục 1", page=1)
    c2 = _chunk(chunk_id=2, source_file="C:/docs/sla.docx",
                section_path="SLA › Điều 2", page=3)
    return [c1, c2]


def test_extract_used_citations_single_index_filters_footer():
    chunks = _two_chunks()
    clean, footer = extract_used_citations(
        f"Trả lời dựa tài liệu 1.\n\n{USED_MARKER}: 1", chunks)
    assert clean == "Trả lời dựa tài liệu 1."
    assert "policy.docx" in footer
    assert "sla.docx" not in footer


def test_extract_used_citations_multiple_indices_keep_retrieval_order():
    chunks = _two_chunks()
    clean, footer = extract_used_citations(
        f"Trả lời dựa cả hai.\n\n{USED_MARKER}: 2,1", chunks)
    assert footer.count("•") == 2
    assert footer.index("policy.docx") < footer.index("sla.docx")


def test_extract_used_citations_missing_marker_falls_back_to_all():
    chunks = _two_chunks()
    clean, footer = extract_used_citations("Trả lời không có marker.", chunks)
    assert clean == "Trả lời không có marker."
    assert "policy.docx" in footer and "sla.docx" in footer


def test_extract_used_citations_out_of_range_indices_fall_back_but_strip_marker():
    chunks = _two_chunks()
    clean, footer = extract_used_citations(
        f"Trả lời.\n\n{USED_MARKER}: 5,9", chunks)
    assert clean == "Trả lời."
    assert USED_MARKER not in clean
    assert "policy.docx" in footer and "sla.docx" in footer


def test_extract_used_citations_duplicate_indices_dedupe():
    chunks = _two_chunks()
    clean, footer = extract_used_citations(
        f"Trả lời.\n\n{USED_MARKER}: 1,1,1", chunks)
    assert footer.count("•") == 1
    assert "policy.docx" in footer


def test_format_context_default_start_numbers_from_one():
    from backend.src.agents.synthesis import _format_context
    text = _format_context(_two_chunks())
    assert text.startswith("[1] ")
    assert "[2] " in text


def test_format_context_custom_start_offsets_numbering():
    from backend.src.agents.synthesis import _format_context
    text = _format_context(_two_chunks(), start=3)
    assert text.startswith("[3] ")
    assert "[4] " in text
    assert "[1] " not in text


import pytest
from unittest.mock import AsyncMock, MagicMock
from backend.src.rag.types import RetrievalResult
from backend.src.agents import synthesis as syn
from backend.tests.conftest import make_mock_llm
from backend.src.agents.synthesis import verify_citations


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


@pytest.mark.asyncio
async def test_synthesize_with_marker_filters_footer_to_used_chunk():
    llm = make_mock_llm(f"Khách được hoàn trong 30 ngày.\n\n{syn.USED_MARKER}: 1")
    c1 = _chunk(chunk_id=1, dense_score=0.7, section_path="Chính sách hoàn hàng › Mục 1",
               source_file="C:/docs/policy.docx", page=1)
    c2 = _chunk(chunk_id=2, dense_score=0.6, section_path="SLA › Điều 2",
               source_file="C:/docs/sla.docx", page=3)
    out = await syn.synthesize("q", _result([c1, c2]), llm)
    assert "Khách được hoàn trong 30 ngày." in out
    assert syn.USED_MARKER not in out
    assert "policy.docx" in out
    assert "sla.docx" not in out


def test_passes_floor_dense_above_floor():
    assert syn.passes_floor(_result([_chunk(dense_score=0.7, sparse_score=None)])) is True


def test_passes_floor_below_and_no_sparse():
    assert syn.passes_floor(_result([_chunk(dense_score=0.2, sparse_score=None)])) is False


def test_passes_floor_sparse_only():
    assert syn.passes_floor(_result([_chunk(dense_score=None, sparse_score=0.1)])) is True


def test_passes_floor_empty():
    assert syn.passes_floor(_result([])) is False


@pytest.mark.asyncio
async def test_verify_citations_empty_chunks_no_llm_call():
    llm = MagicMock(); llm.ainvoke = AsyncMock()
    out = await verify_citations("answer", [], llm)
    assert out == []
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_citations_keeps_yes_drops_no():
    llm = make_mock_llm("1: CÓ\n2: KHÔNG")
    chunks = _two_chunks()
    out = await verify_citations("Trả lời.", chunks, llm)
    assert out == [chunks[0]]


@pytest.mark.asyncio
async def test_verify_citations_all_yes_keeps_all():
    llm = make_mock_llm("1: CÓ\n2: CÓ")
    chunks = _two_chunks()
    out = await verify_citations("Trả lời.", chunks, llm)
    assert out == chunks


@pytest.mark.asyncio
async def test_verify_citations_llm_error_fails_open():
    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=RuntimeError("llm down"))
    chunks = _two_chunks()
    out = await verify_citations("Trả lời.", chunks, llm)
    assert out == chunks


@pytest.mark.asyncio
async def test_verify_citations_unparseable_response_fails_open():
    llm = make_mock_llm("không rõ định dạng gì cả")
    chunks = _two_chunks()
    out = await verify_citations("Trả lời.", chunks, llm)
    assert out == chunks


@pytest.mark.asyncio
async def test_verify_citations_missing_verdict_for_one_chunk_keeps_it():
    llm = make_mock_llm("1: KHÔNG")  # no line for chunk 2
    chunks = _two_chunks()
    out = await verify_citations("Trả lời.", chunks, llm)
    assert out == [chunks[1]]
