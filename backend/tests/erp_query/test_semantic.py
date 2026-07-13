import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query import semantic


class FakeCursor:
    def __init__(self, rows): self._rows = rows
    def fetchall(self): return self._rows


class FakeConn:
    """Trả kết quả theo thứ tự execute: trigram trước, vector sau."""
    def __init__(self, results):
        self.results = list(results); self.queries = []; self.closed = False
    def execute(self, sql, params=None):
        self.queries.append((sql, params)); return FakeCursor(self.results.pop(0))
    def close(self): self.closed = True


def test_normalize_strips_vietnamese_diacritics():
    assert semantic.normalize("Bàn Học Sinh") == "ban hoc sinh"
    # đ/Đ là codepoint riêng (U+0111/U+0110) — NFD không tách, phải replace
    assert semantic.normalize("Đèn LED  chống  cận") == "den led chong can"
    assert semantic.normalize("") == ""


def test_kill_switch_returns_none_without_touching_db(monkeypatch):
    monkeypatch.setenv("ERP_SEMANTIC_RESOLVE", "0")
    def boom():
        raise AssertionError("không được chạm DB khi switch tắt")
    monkeypatch.setattr(semantic._db, "connect", boom)
    assert semantic.semantic_candidates("product.product", "ghe xoay") is None


def test_any_failure_returns_none(monkeypatch):
    monkeypatch.setenv("ERP_SEMANTIC_RESOLVE", "1")
    def boom():
        raise RuntimeError("PG down")
    monkeypatch.setattr(semantic._db, "connect", boom)
    assert semantic.semantic_candidates("product.product", "ghe xoay") is None


def test_rrf_merges_and_ranks_overlap_first(monkeypatch):
    monkeypatch.setenv("ERP_SEMANTIC_RESOLVE", "1")
    tri = [(9, "Ghế xoay", 0.9), (5, "Ghế gỗ", 0.5)]
    vec = [(9, "Ghế xoay", 0.8), (7, "Ghế sofa", 0.6)]
    conn = FakeConn([tri, vec])
    monkeypatch.setattr(semantic._db, "connect", lambda: conn)
    monkeypatch.setattr(semantic._embed, "embed_query", lambda q: [0.0] * 1024)
    out = semantic.semantic_candidates("product.product", "ghe xoay", k=2)
    # id 9 ở cả 2 nguồn → RRF cao nhất; 5 (rank 2 trigram) trước 7 (rank 2
    # vector) vì RRF bằng nhau + sorted ổn định giữ thứ tự chèn
    assert [c["odoo_id"] for c in out] == [9, 5, 7]
    assert out[0] == {"odoo_id": 9, "name": "Ghế xoay"}
    assert conn.closed


def test_trigram_uses_normalized_query(monkeypatch):
    monkeypatch.setenv("ERP_SEMANTIC_RESOLVE", "1")
    conn = FakeConn([[], []])
    monkeypatch.setattr(semantic._db, "connect", lambda: conn)
    monkeypatch.setattr(semantic._embed, "embed_query", lambda q: [0.0] * 1024)
    semantic.semantic_candidates("product.product", "Đèn Bàn", k=3)
    tri_sql, tri_params = conn.queries[0]
    assert "similarity" in tri_sql
    assert tri_params[0] == "den ban"          # query đã normalize
    assert tri_params[1] == "product.product"  # filter model
