import re

from docx import Document
import openpyxl
import pypdf

_HEADING_RE = re.compile(r"^\s*(Chương|Mục|Điều)\b|^\s*\d+(\.\d+)*[\.\)]?\s+\S")


def parse_docx(path: str) -> list[dict]:
    """Blocks in order; a heading block carries heading_level (1..n), body carries None."""
    doc = Document(path)
    blocks: list[dict] = []
    for p in doc.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = (p.style.name or "") if p.style else ""
        level = None
        if style.startswith("Heading"):
            try:
                level = int(style.split()[-1])
            except ValueError:
                level = 1
        blocks.append({"text": text, "heading_level": level, "page": None})
    return blocks


def parse_pdf(path: str) -> list[dict]:
    """Heuristic headings (no font info): numbered/keyword headings & short ALL-CAPS lines."""
    reader = pypdf.PdfReader(path)
    blocks: list[dict] = []
    for pageno, page in enumerate(reader.pages, start=1):
        for line in (page.extract_text() or "").splitlines():
            # pypdf maps some unrecognized glyphs (e.g. a custom bullet-point
            # font) to U+0000 instead of dropping them; Postgres text columns
            # reject NUL bytes outright, so strip them at the source.
            text = line.replace("\x00", "").strip()
            if not text:
                continue
            is_heading = bool(_HEADING_RE.match(text)) or (
                text.isupper() and len(text) <= 80
            )
            blocks.append({"text": text,
                           "heading_level": 2 if is_heading else None,
                           "page": pageno})
    return blocks


def parse_xlsx(path: str) -> list[dict]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheets: list[dict] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(c is not None for c in r)]
        if not rows:
            continue
        header = [str(c) if c is not None else "" for c in rows[0]]
        sheets.append({"sheet": ws.title, "columns": header, "rows": rows[1:]})
    wb.close()
    return sheets
