import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _po(state="purchase", picking_ids=None):
    return {"id": 15, "name": "P00040", "state": state,
            "picking_ids": [] if picking_ids is None else picking_ids}


def _fake(monkeypatch, po_rows, pickings=None, validate_returns=None,
          search_raises=None):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        if model == "purchase.order" and method == "search_read":
            if search_raises:
                raise search_raises
            return po_rows
        if model == "stock.picking" and method == "search_read":
            return pickings or []
        if model == "stock.picking" and method == "button_validate":
            if validate_returns:
                return validate_returns.pop(0)
            return True
        return []

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


def test_not_found(monkeypatch):
    _fake(monkeypatch, [])
    data = _env(fn("receive_order")("P99999"))
    assert data["ok"] is False
    assert "không tìm thấy" in data["display"].lower()


def test_ambiguous(monkeypatch):
    _fake(monkeypatch, [_po(), _po()])
    data = _env(fn("receive_order")("P00040"))
    assert data["ok"] is False
    assert "nhiều đơn" in data["display"].lower()


def test_draft_po_refused(monkeypatch):
    calls = _fake(monkeypatch, [_po(state="draft")])
    data = _env(fn("receive_order")("P00040"))
    assert data["ok"] is False
    assert "chưa xác nhận" in data["display"].lower()
    assert not any(c["model"] == "stock.picking" for c in calls)


def test_no_pickings_pass_through(monkeypatch):
    calls = _fake(monkeypatch, [_po(picking_ids=[])])
    data = _env(fn("receive_order")("P00040"))
    assert data["ok"] is True
    assert "không có phiếu cần nhận" in data["display"].lower()
    assert data["ref"] == "P00040" and data["res_id"] == 15
    assert data["model"] == "purchase.order" and data["state"] == "purchase"
    assert not any(c["method"] == "button_validate" for c in calls)


def test_all_done_pass_through(monkeypatch):
    calls = _fake(monkeypatch, [_po(picking_ids=[60])],
                  pickings=[{"id": 60, "name": "WH/IN/00009", "state": "done"}])
    data = _env(fn("receive_order")("P00040"))
    assert data["ok"] is True
    assert "không có phiếu cần nhận" in data["display"].lower()
    assert not any(c["method"] == "button_validate" for c in calls)


def test_assigned_pickings_validated(monkeypatch):
    # Verified-live: incoming pickings arrive `assigned` right after PO confirm.
    calls = _fake(monkeypatch, [_po(picking_ids=[60, 61])],
                  pickings=[{"id": 60, "name": "WH/IN/00009", "state": "assigned"},
                            {"id": 61, "name": "WH/IN/00010", "state": "assigned"}])
    data = _env(fn("receive_order")("P00040"))
    assert data["ok"] is True
    assert "2 phiếu" in data["display"]
    assert data["ref"] == "P00040"
    validated = [c["args"] for c in calls if c["method"] == "button_validate"]
    assert validated == [[[60]], [[61]]]


def test_pending_not_ready_refused(monkeypatch):
    calls = _fake(monkeypatch, [_po(picking_ids=[60])],
                  pickings=[{"id": 60, "name": "WH/IN/00009", "state": "waiting"}])
    data = _env(fn("receive_order")("P00040"))
    assert data["ok"] is False
    assert "chưa sẵn sàng" in data["display"].lower()
    assert "waiting" in data["display"]
    assert not any(c["method"] == "button_validate" for c in calls)


def test_wizard_dict_stops_validation(monkeypatch):
    calls = _fake(monkeypatch, [_po(picking_ids=[60, 61])],
                  pickings=[{"id": 60, "name": "WH/IN/00009", "state": "assigned"},
                            {"id": 61, "name": "WH/IN/00010", "state": "assigned"}],
                  validate_returns=[{"type": "ir.actions.act_window",
                                     "res_model": "stock.backorder.confirmation"}])
    data = _env(fn("receive_order")("P00040"))
    assert data["ok"] is False
    assert "bổ sung" in data["display"].lower()
    assert "WH/IN/00009" in data["display"]
    validated = [c for c in calls if c["method"] == "button_validate"]
    assert len(validated) == 1


def test_exception_becomes_friendly_error(monkeypatch):
    _fake(monkeypatch, [], search_raises=ValueError("boom"))
    data = _env(fn("receive_order")("P00040"))
    assert data["ok"] is False
    assert "lỗi khi nhận hàng" in data["display"].lower() and "boom" in data["display"]


def test_incoming_filter_pinned(monkeypatch):
    # Outgoing/return pickings of the PO must never be touched.
    calls = _fake(monkeypatch, [_po(picking_ids=[60])],
                  pickings=[{"id": 60, "name": "WH/IN/00009", "state": "assigned"}])
    fn("receive_order")("P00040")
    pick_search = next(c for c in calls
                       if c["model"] == "stock.picking" and c["method"] == "search_read")
    assert ["picking_type_code", "=", "incoming"] in pick_search["args"][0]
    assert ["id", "in", [60]] in pick_search["args"][0]
