import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _so(state="sale", invoice_status="to invoice", invoice_ids=None):
    return {"id": 7, "name": "S00007", "state": state,
            "invoice_status": invoice_status, "invoice_ids": invoice_ids or []}


def _fake(monkeypatch, so_rows, after_ids=None, wizard_raises=None):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        if model == "sale.order" and method == "search_read":
            return so_rows
        if model == "sale.advance.payment.inv" and method == "create":
            if wizard_raises:
                raise wizard_raises
            return 1
        if model == "sale.advance.payment.inv" and method == "create_invoices":
            return None          # gateway swallows the marshal Fault → None
        if model == "sale.order" and method == "read":
            return [{"invoice_ids": after_ids if after_ids is not None else []}]
        return []

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


def test_not_found(monkeypatch):
    _fake(monkeypatch, [])
    data = _env(fn("create_invoice_from_order")("S99999"))
    assert data["ok"] is False
    assert "không tìm thấy" in data["display"].lower()


def test_draft_so_refused_with_hint(monkeypatch):
    calls = _fake(monkeypatch, [_so(state="draft")])
    data = _env(fn("create_invoice_from_order")("S00007"))
    assert data["ok"] is False
    assert "chưa xác nhận" in data["display"].lower()
    assert "xác nhận đơn trước" in data["display"].lower()
    assert not any(c["model"] == "sale.advance.payment.inv" for c in calls)


def test_nothing_to_invoice_refused(monkeypatch):
    calls = _fake(monkeypatch, [_so(invoice_status="no")])
    data = _env(fn("create_invoice_from_order")("S00007"))
    assert data["ok"] is False
    assert "không có gì để xuất hóa đơn" in data["display"].lower()
    assert not any(c["model"] == "sale.advance.payment.inv" for c in calls)


def test_happy_creates_draft_via_wizard(monkeypatch):
    calls = _fake(monkeypatch, [_so(invoice_ids=[3])], after_ids=[3, 61])
    data = _env(fn("create_invoice_from_order")("S00007"))
    assert data["ok"] is True
    assert data["ref"] is None                      # draft has no number yet
    assert data["model"] == "account.move" and data["res_id"] == 61
    assert data["state"] == "draft"
    assert "hóa đơn nháp" in data["display"].lower() and "S00007" in data["display"]
    wiz_create = next(c for c in calls
                      if c["model"] == "sale.advance.payment.inv"
                      and c["method"] == "create")
    assert wiz_create["args"] == [{"advance_payment_method": "delivered"}]
    assert wiz_create["kwargs"]["context"]["active_ids"] == [7]
    assert any(c["method"] == "create_invoices" for c in calls)


def test_no_new_invoice_after_wizard_is_error(monkeypatch):
    _fake(monkeypatch, [_so(invoice_ids=[3])], after_ids=[3])   # nothing new
    data = _env(fn("create_invoice_from_order")("S00007"))
    assert data["ok"] is False
    assert "không tạo được" in data["display"].lower()


def test_wizard_exception_becomes_friendly_error(monkeypatch):
    _fake(monkeypatch, [_so()], wizard_raises=ValueError("boom"))
    data = _env(fn("create_invoice_from_order")("S00007"))
    assert data["ok"] is False
    assert "lỗi" in data["display"].lower() and "boom" in data["display"]


def test_create_invoices_in_method_map():
    assert server.ODOO_METHOD_OPERATION_MAP.get("create_invoices") == "create"
