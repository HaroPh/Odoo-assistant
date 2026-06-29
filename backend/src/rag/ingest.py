import hashlib
import os
import sys

from pyvi import ViTokenizer

from . import db as _db
from .config import RAG_SCHEMA
from .embed import embed_texts
from .parse import parse_docx, parse_pdf, parse_xlsx
from .chunking import chunk_text_blocks, chunk_xlsx_sheets

_EXT = {".pdf": "text", ".docx": "text", ".xlsx": "xlsx"}


def segment_vi(text: str) -> str:
    """pyvi word segmentation; the SAME transform is used at ingest and query time."""
    return ViTokenizer.tokenize(text)


def _hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def _doc_id(path: str) -> str:
    try:
        return os.path.relpath(path).replace("\\", "/")
    except ValueError:
        # Different drive on Windows — use absolute path
        return os.path.abspath(path).replace("\\", "/")


def _chunks_for(path: str, kind: str, doc_id: str) -> list[dict]:
    if kind == "xlsx":
        return chunk_xlsx_sheets(parse_xlsx(path), doc_id=doc_id, source_file=path)
    blocks = parse_pdf(path) if path.lower().endswith(".pdf") else parse_docx(path)
    if not blocks:
        return []
    return chunk_text_blocks(blocks, doc_id=doc_id, source_file=path)


def _ingest_file(path: str, conn) -> dict:
    kind = _EXT.get(os.path.splitext(path)[1].lower())
    if not kind:
        return {"ingested": 0, "skipped": 0, "chunks": 0}
    doc_id, content_hash = _doc_id(path), _hash(path)
    existing = conn.execute(
        "SELECT content_hash FROM rag_documents WHERE doc_id = %s", (doc_id,)
    ).fetchone()
    if existing and existing[0] == content_hash:
        return {"ingested": 0, "skipped": 1, "chunks": 0}
    if existing:
        conn.execute("DELETE FROM rag_documents WHERE doc_id = %s", (doc_id,))  # cascade

    chunks = _chunks_for(path, kind, doc_id)
    if not chunks:
        return {"ingested": 0, "skipped": 1, "chunks": 0}

    conn.execute(
        "INSERT INTO rag_documents (doc_id, source_file, content_hash) VALUES (%s, %s, %s)",
        (doc_id, path, content_hash),
    )
    vectors = embed_texts([c["chunk_text"] for c in chunks])
    for c, vec in zip(chunks, vectors):
        conn.execute(
            "INSERT INTO rag_chunks (doc_id, source_file, doc_title, section_path, page, "
            "sheet, row_range, columns, chunk_index, token_count, chunk_text, embedding, "
            "ts_vector) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, "
            "to_tsvector('simple', %s))",
            (c["doc_id"], c["source_file"], c["doc_title"], c["section_path"], c["page"],
             c["sheet"], c["row_range"], c["columns"], c["chunk_index"], c["token_count"],
             c["chunk_text"], vec, segment_vi(c["chunk_text"])),
        )
    return {"ingested": 1, "skipped": 0, "chunks": len(chunks)}


def ingest_path(path: str, conn=None) -> dict:
    own = conn is None
    if own:
        conn = _db.connect()
        _db.ensure_schema(conn, RAG_SCHEMA)
    try:
        totals = {"ingested": 0, "skipped": 0, "chunks": 0}
        files = ([path] if os.path.isfile(path)
                 else [os.path.join(r, f) for r, _, fs in os.walk(path) for f in fs])
        for f in files:
            for k, v in _ingest_file(f, conn).items():
                totals[k] += v
        return totals
    finally:
        if own:
            conn.close()


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("DOCUMENTS_PATH", ".")
    print(ingest_path(target))


if __name__ == "__main__":
    main()
