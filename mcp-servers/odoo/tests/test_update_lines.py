import json

import server


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _uq():
    return getattr(server.update_quotation_lines, "fn", server.update_quotation_lines)


def _ur():
    return getattr(server.update_rfq_lines, "fn", server.update_rfq_lines)


def _patch(monkeypatch, rows, write_capture):
    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        if method == "search_read":
            return rows
        if method == "write":
            write_capture.append((model, args))
            return True
        raise AssertionError(f"unexpected method {method}")
    monkeypatch.setattr(server, "odoo", fake_odoo)


def test_update_quotation_not_found(monkeypatch):
    _patch(monkeypatch, rows=[], write_capture=[])
    out = _uq()("S99999", [{"op": "add", "product_id": 5, "qty": 2}])
    assert '"ok": false' in out and "không tìm thấy" in out.lower()


def test_update_quotation_refuses_confirmed(monkeypatch):
    writes = []
    _patch(monkeypatch, rows=[{"id": 3, "name": "S003", "state": "sale"}], write_capture=writes)
    out = _uq()("S003", [{"op": "add", "product_id": 5, "qty": 2}])
    assert '"ok": false' in out and "đã xác nhận" in out.lower()
    assert writes == []  # gate blocked the write


def test_update_quotation_add_uses_product_uom_qty(monkeypatch):
    writes = []
    _patch(monkeypatch, rows=[{"id": 3, "name": "S003", "state": "draft"}], write_capture=writes)
    out = _uq()("S003", [{"op": "add", "product_id": 5, "qty": 2}])
    data = _env(out)
    assert data["ok"] is True
    assert data["ref"] == "S003"
    assert data["model"] == "sale.order"
    assert data["res_id"] == 3
    assert data["state"] == "draft"
    model, args = writes[0]
    assert model == "sale.order"
    assert args[1]["order_line"] == [(0, 0, {"product_id": 5, "product_uom_qty": 2})]


def test_update_quotation_remove_and_set_qty_commands(monkeypatch):
    writes = []
    _patch(monkeypatch, rows=[{"id": 3, "name": "S003", "state": "draft"}], write_capture=writes)
    _uq()("S003", [{"op": "remove", "line_id": 10},
                   {"op": "set_qty", "line_id": 11, "qty": 5}])
    cmds = writes[0][1][1]["order_line"]
    assert (2, 10, 0) in cmds
    assert (1, 11, {"product_uom_qty": 5}) in cmds


def test_update_rfq_add_uses_product_qty(monkeypatch):
    writes = []
    _patch(monkeypatch, rows=[{"id": 8, "name": "P008", "state": "draft"}], write_capture=writes)
    out = _ur()("P008", [{"op": "add", "product_id": 5, "qty": 2}])
    data = _env(out)
    assert data["ok"] is True
    assert data["ref"] == "P008"
    assert data["model"] == "purchase.order"
    assert data["res_id"] == 8
    assert data["state"] == "draft"
    model, args = writes[0]
    assert model == "purchase.order"
    assert args[1]["order_line"] == [(0, 0, {"product_id": 5, "product_qty": 2})]


def test_update_quotation_rejects_bad_op(monkeypatch):
    writes = []
    _patch(monkeypatch, rows=[{"id": 3, "name": "S003", "state": "draft"}], write_capture=writes)
    out = _uq()("S003", [{"op": "frobnicate", "line_id": 1}])
    assert '"ok": false' in out
    assert writes == []


def test_update_quotation_empty_ops(monkeypatch):
    writes = []
    _patch(monkeypatch, rows=[{"id": 3, "name": "S003", "state": "draft"}], write_capture=writes)
    out = _uq()("S003", [])
    assert '"ok": false' in out
    assert writes == []


def test_docstrings_do_not_self_censor_confirmed_orders():
    # A prior commit (430a992) had to strip this exact wording from the planner
    # prompt because it made the LLM refuse to even attempt edits on confirmed
    # orders. The MCP docstrings must describe behavior (tool rejects confirmed
    # orders; coordinator offers a flag-note), not instruct the caller to
    # self-select-out — pin so it can't silently regress back in.
    uq_doc = getattr(server.update_quotation_lines, "fn", server.update_quotation_lines).__doc__
    ur_doc = getattr(server.update_rfq_lines, "fn", server.update_rfq_lines).__doc__
    assert "CHƯA xác nhận" not in uq_doc
    assert "CHƯA xác nhận" not in ur_doc


def test_update_quotation_rejects_mixed_valid_invalid_batch(monkeypatch):
    # A valid op followed by an invalid op must reject the WHOLE batch —
    # no partial write of the earlier valid op(s).
    writes = []
    _patch(monkeypatch, rows=[{"id": 3, "name": "S003", "state": "draft"}], write_capture=writes)
    out = _uq()("S003", [{"op": "set_qty", "line_id": 11, "qty": 5},
                         {"op": "frobnicate", "line_id": 1}])
    data = _env(out)
    assert data["ok"] is False
    assert writes == []  # no partial write happened
