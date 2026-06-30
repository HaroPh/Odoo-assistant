from . import db as _db
from .config import TOP_N, TOP_K, RRF_K, RAG_SCHEMA
from .embed import embed_query
from .ingest import segment_vi
from .types import Chunk, RetrievalResult

_COLS = ("id, doc_id, source_file, doc_title, section_path, page, sheet, row_range, chunk_text")


def _dense(conn, qvec) -> list[tuple]:
    return conn.execute(
        f"SELECT {_COLS}, 1 - (embedding <=> %s::vector) AS score "
        f"FROM rag_chunks WHERE embedding IS NOT NULL "
        f"ORDER BY embedding <=> %s::vector LIMIT %s",
        (qvec, qvec, TOP_N),
    ).fetchall()


def _sparse(conn, qseg) -> list[tuple]:
    return conn.execute(
        f"SELECT {_COLS}, ts_rank(ts_vector, plainto_tsquery('simple', %s)) AS score "
        f"FROM rag_chunks WHERE ts_vector @@ plainto_tsquery('simple', %s) "
        f"ORDER BY score DESC LIMIT %s",
        (qseg, qseg, TOP_N),
    ).fetchall()


def _rrf(dense: list[tuple], sparse: list[tuple]) -> dict:
    """Reciprocal Rank Fusion → {row_id: {'row', 'rrf', 'dense', 'sparse'}}."""
    acc: dict = {}
    for rank, row in enumerate(dense):
        acc.setdefault(row[0], {"row": row, "rrf": 0.0, "dense": None, "sparse": None})
        acc[row[0]]["rrf"] += 1.0 / (RRF_K + rank + 1)
        acc[row[0]]["dense"] = float(row[-1])
    for rank, row in enumerate(sparse):
        acc.setdefault(row[0], {"row": row, "rrf": 0.0, "dense": None, "sparse": None})
        acc[row[0]]["rrf"] += 1.0 / (RRF_K + rank + 1)
        acc[row[0]]["sparse"] = float(row[-1])
    return acc


def rerank(query: str, chunks: list[Chunk]) -> list[Chunk]:
    return chunks  # Phase 2: pass-through (non-generative slot for a cross-encoder later)


def compress(query: str, chunks: list[Chunk], k: int) -> list[Chunk]:
    return chunks[:k]  # Phase 2: top-k selection (extractive slot)


def retrieve(query: str, k: int = TOP_K, conn=None) -> RetrievalResult:
    own = conn is None
    if own:
        conn = _db.connect()
        _db.ensure_schema(conn, RAG_SCHEMA)
    try:
        qvec = embed_query(query)
        qseg = segment_vi(query)
        dense, sparse = _dense(conn, qvec), _sparse(conn, qseg)
        fused = _rrf(dense, sparse)
        ordered = sorted(fused.values(), key=lambda e: e["rrf"], reverse=True)

        chunks: list[Chunk] = []
        for rank, e in enumerate(ordered[:k]):
            row = e["row"]
            chunks.append(Chunk(
                chunk_id=row[0], doc_id=row[1], source_file=row[2], doc_title=row[3],
                section_path=row[4], page=row[5], sheet=row[6], row_range=row[7],
                text=row[8], dense_score=e["dense"], sparse_score=e["sparse"],
                rrf_score=e["rrf"], rank=rank))
        chunks = compress(query, rerank(query, chunks), k)
        return RetrievalResult(
            query=query, query_used=qseg, chunks=chunks,
            top_score=chunks[0].rrf_score if chunks else 0.0,
            total_candidates=len(fused), method="hybrid-rrf")
    finally:
        if own:
            conn.close()
