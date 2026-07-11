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
