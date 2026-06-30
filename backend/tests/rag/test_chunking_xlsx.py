import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="module")
def price_xlsx():
    os.makedirs(FIX, exist_ok=True)
    path = os.path.join(FIX, "bang_gia.xlsx")
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Bảng giá"
    ws.append(["Sản phẩm", "Đơn giá", "Hiệu lực"])
    ws.append(["Tủ lớn", 5000000, "2026-01-01"])
    ws.append(["Bàn làm việc", 1200000, "2026-01-01"])
    wb.save(path)
    return path


def test_parse_xlsx_header_and_rows(price_xlsx):
    from backend.src.rag.parse import parse_xlsx
    sheets = parse_xlsx(price_xlsx)
    assert sheets[0]["sheet"] == "Bảng giá"
    assert sheets[0]["columns"] == ["Sản phẩm", "Đơn giá", "Hiệu lực"]
    assert len(sheets[0]["rows"]) == 2


def test_chunk_xlsx_emits_schema_and_header_qualified_rows(price_xlsx):
    from backend.src.rag.parse import parse_xlsx
    from backend.src.rag.chunking import chunk_xlsx_sheets
    chunks = chunk_xlsx_sheets(parse_xlsx(price_xlsx), doc_id="bg", source_file=price_xlsx)
    # exactly one schema chunk for the sheet + one per data row
    schema = [c for c in chunks if c["row_range"] == "schema"]
    assert len(schema) == 1 and "Sản phẩm" in schema[0]["chunk_text"]
    row = next(c for c in chunks if "Tủ lớn" in c["chunk_text"])
    assert "Đơn giá" in row["chunk_text"] and "5000000" in row["chunk_text"]
    assert row["sheet"] == "Bảng giá" and row["columns"] == ["Sản phẩm", "Đơn giá", "Hiệu lực"]
    assert row["section_path"] is None and row["page"] is None
