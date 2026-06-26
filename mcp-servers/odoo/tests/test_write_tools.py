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
