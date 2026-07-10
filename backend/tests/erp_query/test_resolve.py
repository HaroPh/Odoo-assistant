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
