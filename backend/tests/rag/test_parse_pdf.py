import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))


class _FakePage:
    def __init__(self, text):
        self._text = text

    def extract_text(self):
        return self._text


class _FakeReader:
    def __init__(self, pages_text):
        self.pages = [_FakePage(t) for t in pages_text]


def test_parse_pdf_strips_nul_bytes_from_unmapped_glyphs(monkeypatch):
    # pypdf maps some unrecognized glyphs (e.g. a custom bullet-point font) to
    # U+0000 instead of dropping them. Postgres text columns reject NUL bytes
    # outright, so any block still carrying one would crash ingest for the
    # whole file. parse_pdf must sanitize this at the source.
    import pypdf
    from backend.src.rag import parse

    fake = _FakeReader(["\x00 Verify the delivered supplies against the PO."])
    monkeypatch.setattr(pypdf, "PdfReader", lambda path: fake)

    blocks = parse.parse_pdf("irrelevant.pdf")

    assert len(blocks) == 1
    assert "\x00" not in blocks[0]["text"]
    assert blocks[0]["text"] == "Verify the delivered supplies against the PO."


def test_khoan_single_level_number_is_not_heading(monkeypatch):
    # Bug 2026-07-15: khoản luật 1 cấp ("1. ...") bị nhận nhầm heading →
    # chunking nuốt 3212 khoản khỏi index (Điều 124 khoản 1 "quá 90 ngày"
    # biến mất, agent trả lời sai ngưỡng cưỡng chế nợ thuế).
    import pypdf
    from backend.src.rag import parse
    fake = _FakeReader([
        "Điều 124. Trường hợp bị cưỡng chế thi hành quyết định hành chính về quản lý thuế\n"
        "1. Người nộp thuế có tiền thuế nợ quá 90 ngày kể từ ngày hết thời hạn nộp theo quy định."
    ])
    monkeypatch.setattr(pypdf, "PdfReader", lambda path: fake)
    blocks = parse.parse_pdf("x.pdf")
    assert blocks[0]["heading_level"] == 2          # "Điều ..." vẫn là heading
    assert blocks[1]["heading_level"] is None       # khoản 1 cấp = NỘI DUNG


def test_multilevel_numeric_heading_still_detected(monkeypatch):
    # Khóa hành vi giữ lại: numbering đa cấp ("1.1", "3.2.1") vẫn là heading
    # (tài liệu kỹ thuật tương lai).
    import pypdf
    from backend.src.rag import parse
    fake = _FakeReader(["1.1 Giới thiệu hệ thống\n3.2.1. Cấu hình chi tiết"])
    monkeypatch.setattr(pypdf, "PdfReader", lambda path: fake)
    blocks = parse.parse_pdf("x.pdf")
    assert blocks[0]["heading_level"] == 2
    assert blocks[1]["heading_level"] == 2


def test_money_and_hs_code_lines_are_not_headings(monkeypatch):
    # False positive thật tìm thấy trong corpus: số tiền VN (chấm phân cách
    # nghìn) và mã HS trong phụ lục luật đầu tư.
    import pypdf
    from backend.src.rag import parse
    fake = _FakeReader(["5.000.000.000 đồng.\n2931.9080 77-81-6"])
    monkeypatch.setattr(pypdf, "PdfReader", lambda path: fake)
    blocks = parse.parse_pdf("x.pdf")
    assert all(b["heading_level"] is None for b in blocks)
