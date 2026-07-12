from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    chunk_id: int
    doc_id: str
    source_file: str
    doc_title: str
    section_path: str | None   # text docs
    page: int | None           # text docs
    sheet: str | None          # xlsx
    row_range: str | None      # xlsx
    text: str
    dense_score: float | None  # cosine similarity (None if only a sparse hit)
    sparse_score: float | None # ts_rank (None if only a dense hit)
    rrf_score: float           # fused score (RRF) — luôn giữ, kể cả sau rerank
    rank: int                  # 0-based position in FINAL result order
    rerank_score: float | None = None  # cross-encoder score (None nếu tắt/hỏng)


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    query_used: str
    chunks: list[Chunk]
    top_score: float
    total_candidates: int
    method: str = "hybrid-rrf"

    def is_empty(self) -> bool:
        return not self.chunks
