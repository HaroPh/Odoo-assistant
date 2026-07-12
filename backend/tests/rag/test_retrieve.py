import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest


def _seed(conn, rows):
    """rows: list of (doc_id, text, vec). Inserts a doc + one chunk each."""
    for doc_id, text, vec in rows:
        conn.execute("INSERT INTO rag_documents (doc_id, source_file, content_hash) "
                     "VALUES (%s,%s,%s)", (doc_id, f"{doc_id}.docx", doc_id))
        conn.execute(
            "INSERT INTO rag_chunks (doc_id, source_file, doc_title, section_path, "
            "chunk_index, token_count, chunk_text, embedding, ts_vector) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s, to_tsvector('simple', %s))",
            (doc_id, f"{doc_id}.docx", "T", "A › B", 0, 5, text, vec, text))


def test_retrieve_returns_result_with_scores_and_ordering(clean_tables, monkeypatch):
    from backend.src.rag import retrieve as r
    # doc A is the exact dense match; doc B is far
    _seed(clean_tables, [
        ("A", "Khách hàng hoàn hàng trong 30 ngày", [1.0] + [0.0] * 1023),
        ("B", "Quy trình bảo trì máy CNC", [0.0] * 1023 + [1.0]),
    ])
    monkeypatch.setattr(r, "embed_query", lambda q: [1.0] + [0.0] * 1023)

    res = r.retrieve("chính sách hoàn hàng", k=5, conn=clean_tables)
    assert res.method == "hybrid-rrf"
    assert not res.is_empty()
    assert res.chunks[0].doc_id == "A"                 # nearest dense → top
    assert res.top_score == res.chunks[0].rrf_score
    assert res.chunks[0].rank == 0
    assert res.chunks[0].dense_score is not None


def test_retrieve_empty_on_no_match(clean_tables, monkeypatch):
    from backend.src.rag import retrieve as r
    monkeypatch.setattr(r, "embed_query", lambda q: [1.0] + [0.0] * 1023)
    res = r.retrieve("không có gì", k=5, conn=clean_tables)
    assert res.is_empty() and res.top_score == 0.0


# ── Cross-encoder rerank wiring (spec 2026-07-12-rag-reranker) ────────────────
# Mock reranker.score_pairs qua module attr (fake bỏ qua env — autouse
# _rerank_off không ảnh hưởng các test này).


def test_rerank_reorders_and_tags_scores(clean_tables, monkeypatch):
    from backend.src.rag import retrieve as r
    _seed(clean_tables, [
        ("A", "Khách hàng hoàn hàng trong 30 ngày", [1.0] + [0.0] * 1023),
        ("B", "Quy trình bảo trì máy CNC", [1.0, 1.0] + [0.0] * 1022),
    ])
    monkeypatch.setattr(r, "embed_query", lambda q: [1.0] + [0.0] * 1023)
    # A đứng đầu theo RRF; mock cho B điểm cao hơn → B phải lên đầu
    monkeypatch.setattr(r.reranker, "score_pairs",
                        lambda q, texts: [0.1, 0.9])
    res = r.retrieve("hoàn hàng chính sách", k=5, conn=clean_tables)
    assert res.method == "hybrid-rrf+rerank"
    assert res.chunks[0].doc_id == "B"
    assert res.chunks[0].rerank_score == pytest.approx(0.9)
    assert res.chunks[0].rank == 0
    assert res.chunks[1].doc_id == "A"
    assert res.chunks[1].rank == 1
    # invariant giữ nguyên công thức: top_score = rrf của chunk ĐỨNG ĐẦU
    assert res.top_score == res.chunks[0].rrf_score


def test_rerank_pool_wider_than_k(clean_tables, monkeypatch):
    # FIX CHÍNH: chunk hạng-7-theo-RRF (ngoài top-6) phải lọt được vào kết
    # quả khi cross-encoder chấm nó cao nhất — trước fix, rerank chỉ nhận 6
    # chunk đã chốt nên điều này bất khả thi.
    from backend.src.rag import retrieve as r
    rows = []
    for i in range(8):
        marker = " MARKER" if i == 6 else ""
        rows.append((f"D{i}", f"nội dung tài liệu số {i}{marker}",
                     [1.0, float(i)] + [0.0] * 1022))
    _seed(clean_tables, rows)
    # cos(q, D_i) = 1/sqrt(1+i^2) giảm dần theo i → RRF order = D0..D7
    monkeypatch.setattr(r, "embed_query", lambda q: [1.0] + [0.0] * 1023)
    monkeypatch.setattr(r.reranker, "score_pairs",
                        lambda q, texts: [10.0 if "MARKER" in t else 0.0
                                          for t in texts])
    res = r.retrieve("an toàn kho lạnh", k=6, conn=clean_tables)
    ids = [c.doc_id for c in res.chunks]
    assert len(ids) == 6
    assert ids[0] == "D6"                      # hạng-7 RRF lên đầu nhờ rerank
    # sort ổn định: các điểm 0.0 giữ nguyên thứ tự RRF → D0..D4 theo sau
    assert ids == ["D6", "D0", "D1", "D2", "D3", "D4"]
    assert "D5" not in ids and "D7" not in ids
    assert res.chunks[0].rerank_score == pytest.approx(10.0)


def test_rerank_fail_open_keeps_rrf_order(clean_tables, monkeypatch):
    from backend.src.rag import retrieve as r
    _seed(clean_tables, [
        ("A", "Khách hàng hoàn hàng trong 30 ngày", [1.0] + [0.0] * 1023),
        ("B", "Quy trình bảo trì máy CNC", [1.0, 1.0] + [0.0] * 1022),
    ])
    monkeypatch.setattr(r, "embed_query", lambda q: [1.0] + [0.0] * 1023)
    monkeypatch.setattr(r.reranker, "score_pairs", lambda q, texts: None)
    res = r.retrieve("hoàn hàng", k=5, conn=clean_tables)
    assert res.method == "hybrid-rrf"          # không nói dối khi fail-open
    assert res.chunks[0].doc_id == "A"          # nguyên trạng thứ tự RRF
    assert res.chunks[0].rerank_score is None
    assert res.top_score == res.chunks[0].rrf_score
