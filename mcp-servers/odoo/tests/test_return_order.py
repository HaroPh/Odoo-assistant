import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _fake(monkeypatch, responses):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        v = responses.get((model, method), [])
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


_DONE_PICKING = {"id": 147, "name": "WH/OUT/00092", "state": "done"}
_RETURN_LINES = [
    {"id": 1, "product_id": [5, "Office Chair"], "move_quantity": 3.0},
    {"id": 2, "product_id": [8, "Desk Lamp"], "move_quantity": 1.0},
]


def _base(monkeypatch, extra=None, return_lines=None):
    responses = {
        ("stock.picking", "search_read"): [_DONE_PICKING],
        ("stock.return.picking", "create"): 501,
        ("stock.return.picking", "read"): [{"product_return_moves": [1, 2]}],
        ("stock.return.picking.line", "read"): return_lines or _RETURN_LINES,
        ("stock.return.picking.line", "write"): True,
        ("stock.return.picking", "action_create_returns"): {
            "res_id": 156, "res_model": "stock.picking"},
        ("stock.picking", "read"): [{"name": "WH/IN/00057", "state": "assigned"}],
    }
    if extra:
        responses.update(extra)
    return _fake(monkeypatch, responses)


def test_return_order_picking_not_found(monkeypatch):
    _fake(monkeypatch, {("stock.picking", "search_read"): []})
    out = _env(fn("return_order")(999))
    assert out["ok"] is False and "Không tìm thấy" in out["display"]


def test_return_order_picking_not_done(monkeypatch):
    _fake(monkeypatch, {
        ("stock.picking", "search_read"): [
            {"id": 147, "name": "WH/OUT/00092", "state": "assigned"}]})
    out = _env(fn("return_order")(147))
    assert out["ok"] is False and "chưa hoàn tất" in out["display"]


def test_return_order_full_quantity_default_uses_move_quantity(monkeypatch):
    calls = _base(monkeypatch)
    out = _env(fn("return_order")(147))
    assert out["ok"] is True
    assert out["ref"] == "WH/IN/00057" and out["model"] == "stock.picking"
    assert out["res_id"] == 156 and out["state"] == "assigned"
    writes = [c for c in calls if c["method"] == "write"
              and c["model"] == "stock.return.picking.line"]
    assert len(writes) == 2
    assert {"quantity": 3.0} == writes[0]["args"][1]
    assert {"quantity": 1.0} == writes[1]["args"][1]


def test_return_order_partial_quantity_from_lines(monkeypatch):
    calls = _base(monkeypatch)
    out = _env(fn("return_order")(147, [{"product_id": 5, "qty": 2}]))
    assert out["ok"] is True
    writes = [c for c in calls if c["method"] == "write"
              and c["model"] == "stock.return.picking.line"]
    assert len(writes) == 1
    assert writes[0]["args"] == [[1], {"quantity": 2.0}]


def test_return_order_unknown_product_in_lines(monkeypatch):
    _base(monkeypatch)
    out = _env(fn("return_order")(147, [{"product_id": 999, "qty": 1}]))
    assert out["ok"] is False
    assert "không có trong" in out["display"]


def test_return_order_zero_qty_rejected(monkeypatch):
    _base(monkeypatch)
    out = _env(fn("return_order")(147, [{"product_id": 5, "qty": 0}]))
    assert out["ok"] is False and "lớn hơn 0" in out["display"]


def test_return_order_no_res_id_reported_safely(monkeypatch):
    _base(monkeypatch, extra={
        ("stock.return.picking", "action_create_returns"): {"res_id": False}})
    out = _env(fn("return_order")(147))
    assert out["ok"] is False and "kiểm tra trên Odoo" in out["display"]


def test_return_order_odoo_fault(monkeypatch):
    _fake(monkeypatch, {
        ("stock.picking", "search_read"): RuntimeError("Odoo down")})
    out = _env(fn("return_order")(147))
    assert out["ok"] is False and "Odoo down" in out["display"]
