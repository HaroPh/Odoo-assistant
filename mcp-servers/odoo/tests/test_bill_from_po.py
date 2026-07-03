import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _po(state="purchase", invoice_status="to invoice", invoice_ids=None):
    return {"id": 15, "name": "P00040", "state": state,
            "invoice_status": invoice_status,
            "invoice_ids": [] if invoice_ids is None else invoice_ids}


def _fake(monkeypatch, po_rows, after_ids=None, action_raises=None):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        if model == "purchase.order" and method == "search_read":
            return po_rows
        if model == "purchase.order" and method == "action_create_invoice":
            if action_raises:
                raise action_raises
            return None          # action dict / marshal-None — never trusted anyway
        if model == "purchase.order" and method == "read":
            return [{"invoice_ids": after_ids if after_ids is not None else []}]
        if model == "account.move" and method == "write":
            return True
        return []

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


def test_not_found(monkeypatch):
    _fake(monkeypatch, [])
    data = _env(fn("create_bill_from_po")("P99999"))
    assert data["ok"] is False
    assert "không tìm thấy" in data["display"].lower()


def test_draft_po_refused(monkeypatch):
    calls = _fake(monkeypatch, [_po(state="draft")])
    data = _env(fn("create_bill_from_po")("P00040"))
    assert data["ok"] is False
    assert "chưa xác nhận" in data["display"].lower()
    assert not any(c["method"] == "action_create_invoice" for c in calls)


def test_nothing_to_bill_refused(monkeypatch):
    for status in ("no", "invoiced"):
        calls = _fake(monkeypatch, [_po(invoice_status=status)])
        data = _env(fn("create_bill_from_po")("P00040"))
        assert data["ok"] is False
        assert "chưa có gì để lập hóa đơn" in data["display"].lower()
        assert not any(c["method"] == "action_create_invoice" for c in calls)


def test_happy_creates_bill_and_sets_bill_date(monkeypatch):
    calls = _fake(monkeypatch, [_po(invoice_ids=[3])], after_ids=[3, 65])
    data = _env(fn("create_bill_from_po")("P00040"))
    assert data["ok"] is True
    assert data["ref"] is None                      # draft bill has no number
    assert data["model"] == "account.move" and data["res_id"] == 65
    assert data["state"] == "draft"
    assert "hóa đơn ncc" in data["display"].lower() and "P00040" in data["display"]
    assert any(c["method"] == "action_create_invoice" for c in calls)
    # Bill-Date gotcha (verified-live): invoice_date must be written on the new bill
    date_write = next(c for c in calls
                      if c["model"] == "account.move" and c["method"] == "write")
    assert date_write["args"][0] == [65]
    assert "invoice_date" in date_write["args"][1]


def test_no_new_bill_is_error(monkeypatch):
    calls = _fake(monkeypatch, [_po(invoice_ids=[3])], after_ids=[3])
    data = _env(fn("create_bill_from_po")("P00040"))
    assert data["ok"] is False
    assert "không tạo được" in data["display"].lower()
    assert not any(c["model"] == "account.move" and c["method"] == "write"
                   for c in calls)                  # no date write without a bill


def test_action_exception_becomes_friendly_error(monkeypatch):
    _fake(monkeypatch, [_po()], action_raises=ValueError("boom"))
    data = _env(fn("create_bill_from_po")("P00040"))
    assert data["ok"] is False
    assert "lỗi khi tạo hóa đơn" in data["display"].lower() and "boom" in data["display"]


def test_action_create_invoice_in_method_map():
    assert server.ODOO_METHOD_OPERATION_MAP.get("action_create_invoice") == "create"
