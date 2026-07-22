import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _reload_server(monkeypatch):
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"):
        monkeypatch.setenv(k, "x")
    import importlib, server
    importlib.reload(server)
    return server


def _base_calls(calls, picking_state="assigned", validate_result=None):
    def fake_odoo(model, method, args, kwargs=None, **kw):
        calls.append((model, method, args))
        if model == "product.product" and method == "read":
            return [{"id": 552, "name": "Tủ"}]
        if model == "stock.warehouse" and method == "search_read":
            domain = args[0]
            if any(cond[0] == "name" for cond in domain):
                name = domain[0][2]
                if "Shelf 1" in name or "Shelf 2" in name:
                    return []
                return []
            return [{"int_type_id": [7, "Lệnh chuyển hàng nội bộ"]}]
        if model == "stock.location" and method == "search_read":
            term = args[0][1][2]
            if "Shelf 1" in term:
                return [{"id": 17, "complete_name": "WH/Tồn kho/Shelf 1"}]
            if "Shelf 2" in term:
                return [{"id": 16, "complete_name": "WH/Tồn kho/Shelf 2"}]
            return []
        if model == "stock.picking" and method == "create":
            return 501
        if model == "stock.picking" and method in ("action_confirm", "action_assign"):
            return True
        if model == "stock.picking" and method == "read":
            return [{"name": "WH/INT/00001", "state": picking_state}]
        if model == "stock.picking" and method == "button_validate":
            return validate_result if validate_result is not None else True
        return []
    return fake_odoo


def test_happy_path_transfers_between_two_locations(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []
    monkeypatch.setattr(server, "odoo", _base_calls(calls))
    out = server.internal_transfer(qty=5, from_location="Shelf 1",
                                   to_location="Shelf 2", product_id=552)
    assert "Shelf 1" in out and "Shelf 2" in out and "5" in out
    assert ("stock.picking", "create", None) not in [(c[0], c[1], None) for c in calls
                                                       if c[1] != "create"]
    create_calls = [c for c in calls if c[0] == "stock.picking" and c[1] == "create"]
    assert len(create_calls) == 1
    vals = create_calls[0][2][0]
    assert vals["location_id"] == 17 and vals["location_dest_id"] == 16
    assert vals["move_ids"] == [(0, 0, {"product_id": 552, "product_uom_qty": 5})]


def test_missing_from_location_rejected(monkeypatch):
    server = _reload_server(monkeypatch)
    monkeypatch.setattr(server, "odoo", lambda *a, **k: [])
    out = server.internal_transfer(qty=5, from_location="", to_location="Shelf 2",
                                   product_id=552)
    assert "nguồn" in out.lower() and "đích" in out.lower()


def test_zero_qty_rejected(monkeypatch):
    server = _reload_server(monkeypatch)
    monkeypatch.setattr(server, "odoo", lambda *a, **k: [])
    out = server.internal_transfer(qty=0, from_location="Shelf 1",
                                   to_location="Shelf 2", product_id=552)
    assert "không hợp lệ" in out.lower()


def test_same_source_and_dest_rejected(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []
    monkeypatch.setattr(server, "odoo", _base_calls(calls))
    out = server.internal_transfer(qty=5, from_location="Shelf 1",
                                   to_location="Shelf 1", product_id=552)
    assert "trùng nhau" in out.lower()


def test_insufficient_stock_reports_state_not_assigned(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []
    monkeypatch.setattr(server, "odoo", _base_calls(calls, picking_state="confirmed"))
    out = server.internal_transfer(qty=5, from_location="Shelf 1",
                                   to_location="Shelf 2", product_id=552)
    assert "chưa sẵn sàng" in out.lower() or "không đủ" in out.lower()
    assert not any(c[0] == "stock.picking" and c[1] == "button_validate" for c in calls)


def test_wizard_dict_result_reported_safely(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []
    monkeypatch.setattr(server, "odoo",
                        _base_calls(calls, validate_result={"some": "wizard"}))
    out = server.internal_transfer(qty=5, from_location="Shelf 1",
                                   to_location="Shelf 2", product_id=552)
    assert "trực tiếp" in out.lower()


def test_unknown_product_id(monkeypatch):
    server = _reload_server(monkeypatch)
    monkeypatch.setattr(server, "odoo", lambda *a, **k: [])
    out = server.internal_transfer(qty=5, from_location="Shelf 1",
                                   to_location="Shelf 2", product_id=999)
    assert "không tìm thấy" in out.lower()
