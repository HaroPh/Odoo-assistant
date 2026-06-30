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
