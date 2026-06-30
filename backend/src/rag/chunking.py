import re

import tiktoken

from .config import (CHUNK_SIZE_TOKENS, CHUNK_OVERLAP_TOKENS, MIN_CHUNK_TOKENS,
                     TIKTOKEN_ENCODING)

_enc = tiktoken.get_encoding(TIKTOKEN_ENCODING)
_SENT_RE = re.compile(r"(?<=[.!?…])\s+")


def count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def _split_section_text(text: str) -> list[str]:
    """Token-bounded windows over sentences, with overlap; never splits a sentence."""
    sentences = [s for s in _SENT_RE.split(text.strip()) if s]
    chunks: list[str] = []
    cur: list[str] = []
    cur_tok = 0
    for sent in sentences:
        st = count_tokens(sent)
        if cur and cur_tok + st > CHUNK_SIZE_TOKENS:
            chunks.append(" ".join(cur))
            # overlap: keep trailing sentences up to CHUNK_OVERLAP_TOKENS
            keep, ktok = [], 0
            for s in reversed(cur):
                kt = count_tokens(s)
                if ktok + kt > CHUNK_OVERLAP_TOKENS:
                    break
                keep.insert(0, s)
                ktok += kt
            cur, cur_tok = keep[:], ktok
        cur.append(sent)
        cur_tok += st
    if cur:
        chunks.append(" ".join(cur))
    return chunks or ([text.strip()] if text.strip() else [])


def chunk_text_blocks(blocks: list[dict], *, doc_id: str, source_file: str) -> list[dict]:
    """Structure-aware: group body under its heading path, chunk within a leaf section only."""
    doc_title = next((b["text"] for b in blocks if b["heading_level"]), source_file)
    # Build (section_path, page, body_text) leaf sections in order.
    path_stack: list[tuple[int, str]] = []  # (level, text)
    sections: list[tuple[str, int | None, list[str]]] = []
    cur_body: list[str] = []
    cur_page: int | None = None

    def _flush():
        if cur_body:
            crumb = " › ".join(t for _, t in path_stack) or doc_title
            sections.append((crumb, cur_page, cur_body[:]))

    for b in blocks:
        if b["heading_level"]:
            _flush()
            cur_body.clear()
            lvl = b["heading_level"]
            while path_stack and path_stack[-1][0] >= lvl:
                path_stack.pop()
            path_stack.append((lvl, b["text"]))
            cur_page = b["page"]
        else:
            if cur_page is None:
                cur_page = b["page"]
            cur_body.append(b["text"])
    _flush()

    out: list[dict] = []
    idx = 0
    for section_path, page, body in sections:
        text = " ".join(body).strip()
        if not text:
            continue
        pieces = ([text] if count_tokens(text) <= MIN_CHUNK_TOKENS
                  else _split_section_text(text))
        for piece in pieces:
            out.append({
                "doc_id": doc_id, "source_file": source_file, "doc_title": doc_title,
                "section_path": section_path, "page": page,
                "sheet": None, "row_range": None, "columns": None,
                "chunk_index": idx, "token_count": count_tokens(piece),
                "chunk_text": piece,
            })
            idx += 1
    return out


def chunk_xlsx_sheets(sheets: list[dict], *, doc_id: str, source_file: str) -> list[dict]:
    out: list[dict] = []
    idx = 0
    for sh in sheets:
        sheet, columns = sh["sheet"], sh["columns"]

        def _emit(text: str, row_range: str):
            nonlocal idx
            out.append({
                "doc_id": doc_id, "source_file": source_file, "doc_title": sheet,
                "section_path": None, "page": None,
                "sheet": sheet, "row_range": row_range, "columns": columns,
                "chunk_index": idx, "token_count": count_tokens(text),
                "chunk_text": text,
            })
            idx += 1

        _emit(f"[{sheet}] Bảng có các cột: {', '.join(columns)}.", "schema")
        for i, row in enumerate(sh["rows"], start=1):
            pairs = " | ".join(f"{col}: {val}" for col, val in zip(columns, row)
                               if val is not None)
            _emit(f"[{sheet}] {pairs}", f"row {i}")
    return out
