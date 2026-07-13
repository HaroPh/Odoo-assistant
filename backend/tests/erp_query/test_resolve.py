import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query.envelope import ok, err
from backend.src.erp_query.resolve import resolve_entity
from backend.src.erp_query.gateway import Gateway


class FakeTransport:
    def __init__(self, ret): self.ret = ret; self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs)); return self.ret


def _gw(rows): return Gateway(FakeTransport(rows))


def test_envelope_helpers():
    assert ok({"x": 1}, "hi") == {"status": "success", "data": {"x": 1}, "display": "hi"}
    e = err("boom")
    assert e["status"] == "error" and e["data"] is None and e["error"] == "boom"


def test_resolve_single_match_no_disambiguation():
    out = resolve_entity("res.partner", "Azur Interior", gw=_gw([(41, "Azur Interior")]))
    assert out["status"] == "success"
    assert out["data"]["needs_disambiguation"] is False
    assert out["data"]["matches"][0]["id"] == 41


def test_resolve_zero_match():
    out = resolve_entity("res.partner", "Nobody", gw=_gw([]))
    assert out["data"]["matches"] == []
    assert out["data"]["needs_disambiguation"] is False


def test_resolve_multiple_needs_disambiguation():
    out = resolve_entity("res.partner", "Azur",
                         gw=_gw([(41, "Azur Interior"), (52, "Azur Furniture")]))
    assert out["data"]["needs_disambiguation"] is True
    assert len(out["data"]["matches"]) == 2


def test_resolve_multiple_with_one_exact_no_disambiguation():
    out = resolve_entity("res.partner", "Azur Interior",
                         gw=_gw([(41, "Azur Interior"), (52, "Azur Interior Plus")]))
    assert out["data"]["needs_disambiguation"] is False


def test_resolve_blank_query_skips_wildcard_search():
    # Finding 1: chuỗi rỗng/toàn khoảng trắng KHÔNG được chạm name_search — Odoo
    # coi "" là wildcard → trả bừa các bản ghi → disambiguation vô nghĩa. Một
    # truy vấn rỗng phải resolve về KHÔNG CÓ, không phải "tất cả".
    ft = FakeTransport([(1, "khong-duoc-xuat-hien")])
    for q in ("", "   "):
        out = resolve_entity("res.partner", q, gw=Gateway(ft))
        assert out["status"] == "success"
        assert out["data"]["matches"] == []
        assert out["data"]["needs_disambiguation"] is False
    assert ft.calls == []   # transport không bao giờ bị gọi


# ---- Tầng semantic (spec 2026-07-13) — bật switch + mock cả 2 chỗ ----
from backend.src.erp_query import resolve as resolve_mod


class SeqTransport:
    """Mỗi call trả phần tử kế tiếp; dùng khi flow gọi name_search RỒI
    search_read (re-verify)."""
    def __init__(self, rets): self.rets = list(rets); self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs))
        r = self.rets.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _on(monkeypatch, sem=None, scores=None):
    monkeypatch.setenv("ERP_SEMANTIC_RESOLVE", "1")
    monkeypatch.setattr(resolve_mod.semantic, "semantic_candidates",
                        lambda model, query, k=8: sem)
    monkeypatch.setattr(resolve_mod.reranker, "score_pairs",
                        lambda query, texts: scores)


def test_enhanced_merges_semantic_and_reverifies_live_name(monkeypatch):
    # lexical thấy id 5; semantic đề xuất id 9 với tên STALE trong index;
    # re-verify trả tên hiện hành → matches phải dùng tên sống.
    gw = Gateway(SeqTransport([
        [(5, "Office Chair")],                          # name_search
        [{"id": 9, "display_name": "Ghế xoay 360"}],    # search_read re-verify
    ]))
    _on(monkeypatch, sem=[{"odoo_id": 9, "name": "Ghế xoay CŨ"}],
        scores=[0.2, 3.0])   # id 9 điểm cao vượt trội
    out = resolve_entity("product.product", "ghe xoay", gw=gw)
    top = out["data"]["matches"][0]
    assert top["id"] == 9 and top["name"] == "Ghế xoay 360"
    assert out["data"]["needs_disambiguation"] is False   # sigmoid(3.0)≈.95≥.6, gap lớn
    # re-verify đúng 1 lệnh search_read với domain id in [9]
    sr = [c for c in gw._t.calls if c[1] == "search_read"]
    assert sr[0][2] == [[["id", "in", [9]]]]


def test_enhanced_dropped_archived_semantic_id(monkeypatch):
    gw = Gateway(SeqTransport([
        [(5, "Office Chair")],
        [],                    # re-verify: id 9 đã archive → không trả về
    ]))
    _on(monkeypatch, sem=[{"odoo_id": 9, "name": "Ghế xoay"}], scores=[3.0])
    out = resolve_entity("product.product", "office chair", gw=gw)
    assert [m["id"] for m in out["data"]["matches"]] == [5]


def test_enhanced_reverify_error_falls_back_to_lexical(monkeypatch):
    gw = Gateway(SeqTransport([
        [(5, "Office Chair")],
        RuntimeError("odoo hiccup"),   # re-verify nổ → vứt nhánh semantic
    ]))
    _on(monkeypatch, sem=[{"odoo_id": 9, "name": "Ghế xoay"}], scores=[3.0])
    out = resolve_entity("product.product", "office chair", gw=gw)
    assert out["status"] == "success"                      # KHÔNG err()
    assert [m["id"] for m in out["data"]["matches"]] == [5]


def test_enhanced_below_floor_needs_disambiguation(monkeypatch):
    gw = Gateway(SeqTransport([[(5, "Ghế A"), (6, "Ghế B")]]))
    _on(monkeypatch, sem=None, scores=[0.1, 0.05])   # sigmoid ≈ .52/.51 < .6
    out = resolve_entity("product.product", "ghe", gw=gw)
    assert out["data"]["needs_disambiguation"] is True


def test_enhanced_small_gap_needs_disambiguation(monkeypatch):
    gw = Gateway(SeqTransport([[(5, "Ghế A"), (6, "Ghế B")]]))
    _on(monkeypatch, sem=None, scores=[2.0, 1.9])    # ≈ .88/.87 — gap < .15
    out = resolve_entity("product.product", "ghe", gw=gw)
    assert out["data"]["needs_disambiguation"] is True


def test_enhanced_single_exact_match_short_circuits(monkeypatch):
    # exact duy nhất → auto-pick bất kể gap (rule cũ, ưu tiên cao nhất)
    gw = Gateway(SeqTransport([[(5, "Ghế xoay"), (6, "Ghế xoay pro")]]))
    _on(monkeypatch, sem=None, scores=[2.0, 1.95])   # gap nhỏ nhưng có exact
    out = resolve_entity("product.product", "Ghế xoay", gw=gw)
    assert out["data"]["needs_disambiguation"] is False


def test_enhanced_reranker_none_uses_legacy_rule_on_merged(monkeypatch):
    gw = Gateway(SeqTransport([[(5, "Ghế A"), (6, "Ghế B")]]))
    _on(monkeypatch, sem=None, scores=None)          # reranker tắt/hỏng
    out = resolve_entity("product.product", "ghe", gw=gw)
    assert out["data"]["needs_disambiguation"] is True   # rule cũ: >1, không exact
    assert all(0.0 <= m["score"] <= 1.0 for m in out["data"]["matches"])


def test_kill_switch_never_calls_semantic_or_reranker(monkeypatch):
    # fixture autouse đã set "0" — chốt chặn: 2 tầng mới không được đụng tới
    def boom(*a, **k):
        raise AssertionError("không được gọi khi switch tắt")
    monkeypatch.setattr(resolve_mod.semantic, "semantic_candidates", boom)
    monkeypatch.setattr(resolve_mod.reranker, "score_pairs", boom)
    out = resolve_entity("product.product", "Office Chair",
                         gw=_gw([(5, "Office Chair")]))
    assert out["data"]["matches"][0]["id"] == 5


def test_non_product_model_stays_legacy_even_when_enabled(monkeypatch):
    monkeypatch.setenv("ERP_SEMANTIC_RESOLVE", "1")
    def boom(*a, **k):
        raise AssertionError("res.partner không đi tầng semantic")
    monkeypatch.setattr(resolve_mod.semantic, "semantic_candidates", boom)
    monkeypatch.setattr(resolve_mod.reranker, "score_pairs", boom)
    out = resolve_entity("res.partner", "Azur", gw=_gw([(41, "Azur Interior")]))
    assert out["data"]["matches"][0]["id"] == 41


def test_enhanced_envelope_shape_unchanged(monkeypatch):
    gw = Gateway(SeqTransport([[(5, "Ghế A")]]))
    _on(monkeypatch, sem=None, scores=[3.0])
    out = resolve_entity("product.product", "ghe", gw=gw)
    assert set(out["data"].keys()) == {"matches", "needs_disambiguation"}
    assert set(out["data"]["matches"][0].keys()) == {"id", "name", "score"}
