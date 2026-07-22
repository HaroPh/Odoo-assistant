import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _reload_server(monkeypatch):
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"):
        monkeypatch.setenv(k, "x")
    import importlib, server
    importlib.reload(server)
    return server


def _base_calls(calls, validate_result=True):
    def fake_odoo(model, method, args, kwargs=None, **kw):
        calls.append((model, method, args))
        if model == "product.product" and method == "read":
            return [{"id": 552, "name": "Tủ"}]
        if model == "stock.warehouse" and method == "search_read":
            return [{"lot_stock_id": [8, "WH/Tồn kho"]}]
        if model == "stock.scrap" and method == "create":
            return 701
        if model == "stock.scrap" and method == "action_validate":
            return validate_result
        return []
    return fake_odoo


def test_happy_path_scraps_at_default_location(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []
    monkeypatch.setattr(server, "odoo", _base_calls(calls))
    out = server.scrap_product(qty=3, product_id=552, reason="hàng vỡ")
    assert "3" in out and "Tủ" in out
    create_calls = [c for c in calls if c[0] == "stock.scrap" and c[1] == "create"]
    assert len(create_calls) == 1
    vals = create_calls[0][2][0]
    assert vals["product_id"] == 552 and vals["scrap_qty"] == 3
    assert vals["location_id"] == 8
    assert vals["origin"] == "hàng vỡ"


def test_reason_omitted_not_sent(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []
    monkeypatch.setattr(server, "odoo", _base_calls(calls))
    server.scrap_product(qty=1, product_id=552)
    vals = [c for c in calls if c[0] == "stock.scrap" and c[1] == "create"][0][2][0]
    assert "origin" not in vals


def test_zero_qty_rejected(monkeypatch):
    server = _reload_server(monkeypatch)
    monkeypatch.setattr(server, "odoo", lambda *a, **k: [])
    out = server.scrap_product(qty=0, product_id=552)
    assert "không hợp lệ" in out.lower()


def test_insufficient_stock_wizard_reported_safely(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []
    monkeypatch.setattr(server, "odoo",
                        _base_calls(calls, validate_result={"some": "wizard"}))
    out = server.scrap_product(qty=999, product_id=552)
    assert "không đủ" in out.lower() or "trực tiếp" in out.lower()


def test_unknown_product_id(monkeypatch):
    server = _reload_server(monkeypatch)
    monkeypatch.setattr(server, "odoo", lambda *a, **k: [])
    out = server.scrap_product(qty=1, product_id=999)
    assert "không tìm thấy" in out.lower()
