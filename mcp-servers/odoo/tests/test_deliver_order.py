import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _so(state="sale", picking_ids=None):
    return {"id": 9, "name": "S00040", "state": state,
            "picking_ids": [] if picking_ids is None else picking_ids}


def _fake(monkeypatch, so_rows, pickings=None, validate_returns=None,
          search_raises=None):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        if model == "sale.order" and method == "search_read":
            if search_raises:
                raise search_raises
            return so_rows
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
    data = _env(fn("deliver_order")("S99999"))
    assert data["ok"] is False
    assert "không tìm thấy" in data["display"].lower()


def test_ambiguous(monkeypatch):
    _fake(monkeypatch, [_so(), _so()])
    data = _env(fn("deliver_order")("S00040"))
    assert data["ok"] is False
    assert "nhiều đơn" in data["display"].lower()


def test_draft_so_refused(monkeypatch):
    calls = _fake(monkeypatch, [_so(state="draft")])
    data = _env(fn("deliver_order")("S00040"))
    assert data["ok"] is False
    assert "chưa xác nhận" in data["display"].lower()
    assert not any(c["model"] == "stock.picking" for c in calls)


def test_no_pickings_pass_through(monkeypatch):
    # Service / order-policy order: no outgoing picking at all → the chain
    # must continue to "Tạo hóa đơn", so this is ok=true, NOT a failure.
    calls = _fake(monkeypatch, [_so(picking_ids=[])])
    data = _env(fn("deliver_order")("S00040"))
    assert data["ok"] is True
    assert "không có phiếu cần giao" in data["display"].lower()
    assert data["ref"] == "S00040" and data["res_id"] == 9
    assert data["model"] == "sale.order" and data["state"] == "sale"
    assert not any(c["method"] == "button_validate" for c in calls)


def test_all_done_pass_through(monkeypatch):
    calls = _fake(monkeypatch, [_so(picking_ids=[31])],
                  pickings=[{"id": 31, "name": "WH/OUT/00031", "state": "done"}])
    data = _env(fn("deliver_order")("S00040"))
    assert data["ok"] is True
    assert "không có phiếu cần giao" in data["display"].lower()
    assert not any(c["method"] == "button_validate" for c in calls)


def test_assigned_pickings_validated(monkeypatch):
    calls = _fake(monkeypatch, [_so(picking_ids=[31, 32])],
                  pickings=[{"id": 31, "name": "WH/OUT/00031", "state": "assigned"},
                            {"id": 32, "name": "WH/OUT/00032", "state": "assigned"}])
    data = _env(fn("deliver_order")("S00040"))
    assert data["ok"] is True
    assert "2 phiếu" in data["display"]
    assert data["ref"] == "S00040"
    validated = [c["args"] for c in calls if c["method"] == "button_validate"]
    assert validated == [[[31]], [[32]]]


def test_pending_not_reserved_refused(monkeypatch):
    calls = _fake(monkeypatch, [_so(picking_ids=[31])],
                  pickings=[{"id": 31, "name": "WH/OUT/00031", "state": "confirmed"}])
    data = _env(fn("deliver_order")("S00040"))
    assert data["ok"] is False
    assert "chưa reserve" in data["display"].lower()
    assert "confirmed" in data["display"]
    assert not any(c["method"] == "button_validate" for c in calls)


def test_wizard_dict_stops_validation(monkeypatch):
    calls = _fake(monkeypatch, [_so(picking_ids=[31, 32])],
                  pickings=[{"id": 31, "name": "WH/OUT/00031", "state": "assigned"},
                            {"id": 32, "name": "WH/OUT/00032", "state": "assigned"}],
                  validate_returns=[{"type": "ir.actions.act_window",
                                     "res_model": "stock.backorder.confirmation"}])
    data = _env(fn("deliver_order")("S00040"))
    assert data["ok"] is False
    assert "bổ sung" in data["display"].lower()
    assert "WH/OUT/00031" in data["display"]
    validated = [c for c in calls if c["method"] == "button_validate"]
    assert len(validated) == 1              # stopped after the wizard dict


def test_exception_becomes_friendly_error(monkeypatch):
    _fake(monkeypatch, [], search_raises=ValueError("boom"))
    data = _env(fn("deliver_order")("S00040"))
    assert data["ok"] is False
    assert "lỗi khi giao hàng" in data["display"].lower() and "boom" in data["display"]


def test_outgoing_filter_pinned(monkeypatch):
    # Return/incoming pickings of the SO must never be touched.
    calls = _fake(monkeypatch, [_so(picking_ids=[31])],
                  pickings=[{"id": 31, "name": "WH/OUT/00031", "state": "assigned"}])
    fn("deliver_order")("S00040")
    pick_search = next(c for c in calls
                       if c["model"] == "stock.picking" and c["method"] == "search_read")
    assert ["picking_type_code", "=", "outgoing"] in pick_search["args"][0]
    assert ["id", "in", [31]] in pick_search["args"][0]
