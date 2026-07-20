import json

import pytest

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


class Seq:
    """Trả lần lượt từng phần tử cho các lần gọi lặp lại cùng (model, method)."""
    def __init__(self, *items):
        self.items = list(items)

    def next(self):
        return self.items.pop(0)


def _fake(monkeypatch, responses):
    """responses: {(model, method): value | Seq(...) | Exception}. Ghi lại
    calls để assert NỘI DUNG args (bài học round 1: mock bỏ qua domain/vals
    từng che bug product_tmpl_id)."""
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        v = responses.get((model, method), [])
        if isinstance(v, Exception):
            raise v
        return v.next() if isinstance(v, Seq) else v

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


_PRODUCT = [{"id": 39, "name": "[FURN_8855] Drawer",
             "product_tmpl_id": [24, "[FURN_8855] Drawer"]}]
_BOM_PRIM = {"id": 7, "code": "PRIM-ASSEM", "type": "normal",
             "product_tmpl_id": [24, "[FURN_8855] Drawer"], "active": True}


def _mo(state="confirmed", raw_ids=(297, 298)):
    return [{"id": 7, "name": "WH/MO/00007", "state": state,
             "product_id": [39, "[FURN_8855] Drawer"], "product_qty": 2.0,
             "move_raw_ids": list(raw_ids)}]


def _move(mid, demand=2.0, qty=2.0, state="assigned",
          name="[FURN_2100] Drawer Black"):
    return {"id": mid, "product_id": [67, name], "product_uom_qty": demand,
            "quantity": qty, "state": state}


# ── create_manufacturing_order ───────────────────────────────────────────────

def test_create_mo_rejects_nonpositive_qty(monkeypatch):
    calls = _fake(monkeypatch, {})
    out = _env(fn("create_manufacturing_order")(39, 0))
    assert out["ok"] is False and "lớn hơn 0" in out["display"]
    assert calls == []


def test_create_mo_product_not_found(monkeypatch):
    _fake(monkeypatch, {("product.product", "search_read"): []})
    out = _env(fn("create_manufacturing_order")(999, 2))
    assert out["ok"] is False and "Không tìm thấy sản phẩm ID 999" in out["display"]


def test_create_mo_explicit_bom_wrong_template_rejected(monkeypatch):
    # Bẫy id-space: BoM thuộc template khác (template 39 = Table Top ≠
    # template 24 của variant 39 Drawer) phải bị chặn.
    _fake(monkeypatch, {
        ("product.product", "search_read"): _PRODUCT,
        ("mrp.bom", "search_read"): [{"id": 3, "code": None, "type": "normal",
                                      "product_tmpl_id": [39, "Table Top"],
                                      "active": True}],
    })
    out = _env(fn("create_manufacturing_order")(39, 2, 3))
    assert out["ok"] is False and "không thuộc sản phẩm này" in out["display"]


def test_create_mo_explicit_bom_kit_rejected(monkeypatch):
    _fake(monkeypatch, {
        ("product.product", "search_read"): _PRODUCT,
        ("mrp.bom", "search_read"): [{"id": 6, "code": None, "type": "phantom",
                                      "product_tmpl_id": [24, "Drawer"],
                                      "active": True}],
    })
    out = _env(fn("create_manufacturing_order")(39, 2, 6))
    assert out["ok"] is False and "Kit" in out["display"]


def test_create_mo_explicit_bom_not_found(monkeypatch):
    _fake(monkeypatch, {
        ("product.product", "search_read"): _PRODUCT,
        ("mrp.bom", "search_read"): [],
    })
    out = _env(fn("create_manufacturing_order")(39, 2, 12345))
    assert out["ok"] is False and "Không tìm thấy BoM 12345" in out["display"]


def test_create_mo_auto_bom_none_found(monkeypatch):
    _fake(monkeypatch, {
        ("product.product", "search_read"): _PRODUCT,
        ("mrp.bom", "search_read"): [],
    })
    out = _env(fn("create_manufacturing_order")(39, 2))
    assert out["ok"] is False and "chưa có định mức" in out["display"]


def test_create_mo_auto_bom_multiple_lists_candidates(monkeypatch):
    _fake(monkeypatch, {
        ("product.product", "search_read"): _PRODUCT,
        ("mrp.bom", "search_read"): [
            {"id": 7, "code": "PRIM-ASSEM"}, {"id": 8, "code": "SEC-ASSEM"}],
    })
    out = _env(fn("create_manufacturing_order")(39, 2))
    assert out["ok"] is False
    assert "PRIM-ASSEM" in out["display"] and "SEC-ASSEM" in out["display"]
    assert "chỉ rõ BoM" in out["display"]


def test_create_mo_happy_path_creates_with_minimal_vals(monkeypatch):
    calls = _fake(monkeypatch, {
        ("product.product", "search_read"): _PRODUCT,
        ("mrp.bom", "search_read"): [{"id": 7, "code": "PRIM-ASSEM"}],
        ("mrp.production", "create"): Seq(7),
        ("mrp.production", "search_read"): [{"id": 7, "name": "WH/MO/00007",
                                             "state": "draft"}],
    })
    out = _env(fn("create_manufacturing_order")(39, 2))
    assert out["ok"] is True
    assert out["ref"] == "WH/MO/00007" and out["model"] == "mrp.production"
    assert out["res_id"] == 7 and out["state"] == "draft"
    create = next(c for c in calls if c["method"] == "create")
    # Vals tối thiểu ĐÚNG như probe #1 — không field thừa
    assert create["args"] == [{"product_id": 39, "product_qty": 2.0,
                               "bom_id": 7, "origin": "AI Agent"}]
    bom_search = next(c for c in calls if c["model"] == "mrp.bom")
    # Bẫy id-space: domain đi qua TEMPLATE id 24, không phải variant 39
    assert ["product_tmpl_id", "=", 24] in bom_search["args"][0]
    assert ["type", "=", "normal"] in bom_search["args"][0]


# ── confirm_manufacturing_order ──────────────────────────────────────────────

def test_confirm_mo_not_found(monkeypatch):
    _fake(monkeypatch, {("mrp.production", "search_read"): []})
    out = _env(fn("confirm_manufacturing_order")("WH/MO/99999"))
    assert out["ok"] is False and "Không tìm thấy" in out["display"]


def test_confirm_mo_duplicate_ref(monkeypatch):
    _fake(monkeypatch, {("mrp.production", "search_read"): _mo() + _mo()})
    out = _env(fn("confirm_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False and "nhiều lệnh sản xuất" in out["display"]


def test_confirm_mo_done_refused(monkeypatch):
    _fake(monkeypatch, {("mrp.production", "search_read"): _mo(state="done")})
    out = _env(fn("confirm_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False and "đã hoàn tất" in out["display"]


def test_confirm_mo_cancelled_refused(monkeypatch):
    _fake(monkeypatch, {("mrp.production", "search_read"): _mo(state="cancel")})
    out = _env(fn("confirm_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False and "đã bị hủy" in out["display"]


def test_confirm_mo_already_confirmed(monkeypatch):
    _fake(monkeypatch, {("mrp.production", "search_read"): _mo(state="confirmed")})
    out = _env(fn("confirm_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False and "đã được xác nhận" in out["display"]


def test_confirm_mo_draft_confirms(monkeypatch):
    calls = _fake(monkeypatch, {
        ("mrp.production", "search_read"): _mo(state="draft"),
        ("mrp.production", "action_confirm"): True,
    })
    out = _env(fn("confirm_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is True and out["state"] == "confirmed"
    assert out["ref"] == "WH/MO/00007"
    confirm = next(c for c in calls if c["method"] == "action_confirm")
    assert confirm["args"] == [[7]]


# ── complete_manufacturing_order ─────────────────────────────────────────────

def test_complete_mo_draft_refused(monkeypatch):
    _fake(monkeypatch, {("mrp.production", "search_read"): _mo(state="draft")})
    out = _env(fn("complete_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False and "chưa xác nhận" in out["display"]


def test_complete_mo_done_refused(monkeypatch):
    _fake(monkeypatch, {("mrp.production", "search_read"): _mo(state="done")})
    out = _env(fn("complete_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False and "đã hoàn tất" in out["display"]


def test_complete_mo_cancelled_refused(monkeypatch):
    _fake(monkeypatch, {("mrp.production", "search_read"): _mo(state="cancel")})
    out = _env(fn("complete_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False and "đã bị hủy" in out["display"]


def test_complete_mo_happy_path_marks_done(monkeypatch):
    calls = _fake(monkeypatch, {
        ("mrp.production", "search_read"): Seq(
            _mo(state="confirmed"),                 # lookup ban đầu
            [{"id": 7, "state": "done"}],           # re-read xác minh sau mark_done
        ),
        ("stock.move", "search_read"): [_move(297), _move(298)],
        ("mrp.production", "button_mark_done"): True,
    })
    out = _env(fn("complete_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is True and out["state"] == "done"
    assert "nhập kho 2" in out["display"]
    mark = next(c for c in calls if c["method"] == "button_mark_done")
    assert mark["args"] == [[7]]
    # đủ nguyên liệu → KHÔNG gọi action_assign
    assert not any(c["method"] == "action_assign" for c in calls)


def test_complete_mo_shortage_assigns_once_then_refuses(monkeypatch):
    shortage = [_move(297, demand=500.0, qty=41.0, state="partially_available"),
                _move(298, demand=500.0, qty=41.0, state="partially_available",
                      name="[FURN_5623] Drawer Case Black")]
    calls = _fake(monkeypatch, {
        ("mrp.production", "search_read"): _mo(state="confirmed"),
        ("stock.move", "search_read"): Seq(shortage, shortage),  # trước & sau assign
        ("mrp.production", "action_assign"): True,
    })
    out = _env(fn("complete_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False
    assert "Chưa đủ nguyên liệu" in out["display"]
    assert "cần 500, sẵn sàng 41" in out["display"]
    assert "tạo đơn mua" in out["display"]
    # action_assign gọi ĐÚNG 1 lần, KHÔNG mark_done
    assert sum(1 for c in calls if c["method"] == "action_assign") == 1
    assert not any(c["method"] == "button_mark_done" for c in calls)


def test_complete_mo_not_done_after_mark_reports_state(monkeypatch):
    _fake(monkeypatch, {
        ("mrp.production", "search_read"): Seq(
            _mo(state="confirmed"),
            [{"id": 7, "state": "progress"}],       # safety net: chưa done thật
        ),
        ("stock.move", "search_read"): [_move(297), _move(298)],
        ("mrp.production", "button_mark_done"): True,
    })
    out = _env(fn("complete_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False and "progress" in out["display"]


def test_complete_mo_odoo_error_returns_message(monkeypatch):
    _fake(monkeypatch, {
        ("mrp.production", "search_read"): RuntimeError("Odoo down"),
    })
    out = _env(fn("complete_manufacturing_order")("WH/MO/00007"))
    assert out["ok"] is False and "Odoo down" in out["display"]


# ── security allowlist ───────────────────────────────────────────────────────

def test_security_map_has_mrp_methods():
    from security import classify_operation
    assert classify_operation("button_mark_done") == "write"
    assert classify_operation("action_assign") == "write"
