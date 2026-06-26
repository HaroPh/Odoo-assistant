import server


def patch_odoo(monkeypatch, by_model, confirm_capture=None):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method, "args": args})
        if confirm_capture is not None and method in (
            "button_confirm", "action_post",
            "action_set_quantities_to_reservation", "button_validate",
        ):
            confirm_capture.append((model, method, args))
            return True
        return by_model.get((model, method), by_model.get(model, []))

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


# ── confirm_purchase_order ────────────────────────────────────────────────────

def test_confirm_po_not_found(monkeypatch):
    patch_odoo(monkeypatch, {"purchase.order": []})
    out = fn("confirm_purchase_order")("P99999")
    assert "không tìm thấy" in out.lower()


def test_confirm_po_ambiguous(monkeypatch):
    patch_odoo(monkeypatch, {"purchase.order": [
        {"id": 1, "name": "P001", "state": "draft"},
        {"id": 2, "name": "P001", "state": "draft"},
    ]})
    assert "nhiều" in fn("confirm_purchase_order")("P001").lower()


def test_confirm_po_already_confirmed_idempotent(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"purchase.order": [{"id": 3, "name": "P00003", "state": "purchase"}]},
               confirm_capture=cap)
    out = fn("confirm_purchase_order")("P00003")
    assert "đã được xác nhận" in out.lower()
    assert cap == []  # button_confirm NOT called


def test_confirm_po_cancelled(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"purchase.order": [{"id": 4, "name": "P00004", "state": "cancel"}]},
               confirm_capture=cap)
    out = fn("confirm_purchase_order")("P00004")
    assert "hủy" in out.lower()
    assert cap == []


def test_confirm_po_draft_calls_button_confirm(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"purchase.order": [{"id": 5, "name": "P00005", "state": "draft"}]},
               confirm_capture=cap)
    out = fn("confirm_purchase_order")("P00005")
    assert "đã xác nhận" in out.lower()
    assert ("purchase.order", "button_confirm", [[5]]) in cap


def test_confirm_po_sent_calls_button_confirm(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"purchase.order": [{"id": 6, "name": "P00006", "state": "sent"}]},
               confirm_capture=cap)
    out = fn("confirm_purchase_order")("P00006")
    assert "đã xác nhận" in out.lower()
    assert ("purchase.order", "button_confirm", [[6]]) in cap


# ── post_invoice ──────────────────────────────────────────────────────────────

def test_post_invoice_not_found(monkeypatch):
    patch_odoo(monkeypatch, {"account.move": []})
    assert "không tìm thấy" in fn("post_invoice")("INV/2026/99999").lower()


def test_post_invoice_ambiguous(monkeypatch):
    patch_odoo(monkeypatch, {"account.move": [
        {"id": 1, "name": "INV/2026/00001", "state": "draft", "move_type": "out_invoice"},
        {"id": 2, "name": "INV/2026/00001", "state": "draft", "move_type": "out_invoice"},
    ]})
    assert "nhiều" in fn("post_invoice")("INV/2026/00001").lower()


def test_post_invoice_already_posted_idempotent(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"account.move": [{"id": 10, "name": "INV/2026/00001",
                                   "state": "posted", "move_type": "out_invoice"}]},
               confirm_capture=cap)
    out = fn("post_invoice")("INV/2026/00001")
    assert "đã được phát hành" in out.lower()
    assert cap == []  # action_post NOT called


def test_post_invoice_cancelled(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"account.move": [{"id": 11, "name": "INV/2026/00002",
                                   "state": "cancel", "move_type": "out_invoice"}]},
               confirm_capture=cap)
    out = fn("post_invoice")("INV/2026/00002")
    assert "hủy" in out.lower()
    assert cap == []


def test_post_invoice_draft_calls_action_post(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"account.move": [{"id": 12, "name": "INV/2026/00003",
                                   "state": "draft", "move_type": "out_invoice"}]},
               confirm_capture=cap)
    out = fn("post_invoice")("INV/2026/00003")
    assert "đã phát hành" in out.lower()
    assert ("account.move", "action_post", [[12]]) in cap


# ── validate_picking ──────────────────────────────────────────────────────────

def test_validate_picking_not_found(monkeypatch):
    patch_odoo(monkeypatch, {"stock.picking": []})
    assert "không tìm thấy" in fn("validate_picking")("WH/OUT/99999").lower()


def test_validate_picking_ambiguous(monkeypatch):
    patch_odoo(monkeypatch, {"stock.picking": [
        {"id": 1, "name": "WH/OUT/0001", "state": "assigned"},
        {"id": 2, "name": "WH/OUT/0001", "state": "assigned"},
    ]})
    assert "nhiều" in fn("validate_picking")("WH/OUT/0001").lower()


def test_validate_picking_done_idempotent(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"stock.picking": [{"id": 10, "name": "WH/OUT/0010",
                                    "state": "done"}]},
               confirm_capture=cap)
    out = fn("validate_picking")("WH/OUT/0010")
    assert "đã được xác nhận" in out.lower()
    assert cap == []


def test_validate_picking_cancelled(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"stock.picking": [{"id": 11, "name": "WH/OUT/0011",
                                    "state": "cancel"}]},
               confirm_capture=cap)
    out = fn("validate_picking")("WH/OUT/0011")
    assert "hủy" in out.lower()
    assert cap == []


def test_validate_picking_not_assigned_refused(monkeypatch):
    for bad_state in ("draft", "waiting", "confirmed"):
        cap = []
        patch_odoo(monkeypatch,
                   {"stock.picking": [{"id": 20, "name": "WH/OUT/0020",
                                        "state": bad_state}]},
                   confirm_capture=cap)
        out = fn("validate_picking")("WH/OUT/0020")
        assert "chưa sẵn sàng" in out.lower() or "chưa" in out.lower()
        assert cap == []


def test_validate_picking_assigned_calls_set_qty_then_validate(monkeypatch):
    """assigned → set_quantities_to_reservation THEN button_validate, in order."""
    call_order = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        if method == "search_read":
            return [{"id": 30, "name": "WH/OUT/0030", "state": "assigned"}]
        call_order.append(method)
        return True  # both write calls succeed

    monkeypatch.setattr(server, "odoo", fake_odoo)
    out = fn("validate_picking")("WH/OUT/0030")
    assert call_order == ["action_set_quantities_to_reservation", "button_validate"]
    assert "đã xác nhận" in out.lower()


def test_validate_picking_wizard_fallback(monkeypatch):
    """If button_validate returns a dict (unexpected wizard), emit safe message."""
    call_order = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        if method == "search_read":
            return [{"id": 31, "name": "WH/OUT/0031", "state": "assigned"}]
        call_order.append(method)
        if method == "button_validate":
            return {"type": "ir.actions.act_window", "res_model": "stock.backorder.confirmation"}
        return True

    monkeypatch.setattr(server, "odoo", fake_odoo)
    out = fn("validate_picking")("WH/OUT/0031")
    assert "cần thao tác bổ sung" in out.lower() or "bổ sung" in out.lower()
    # Both methods were still called
    assert "action_set_quantities_to_reservation" in call_order
    assert "button_validate" in call_order
