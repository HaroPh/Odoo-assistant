import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _reload_server(monkeypatch):
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"):
        monkeypatch.setenv(k, "x")
    import importlib, server
    importlib.reload(server)
    return server


def _base_stock_odoo(calls):
    def fake_odoo(model, method, args, kwargs=None, **kw):
        calls.append((model, method))
        if model == "product.product" and method == "read":
            return [{"id": 552, "name": "Tủ", "is_storable": True}]
        if model == "stock.warehouse":
            return [{"lot_stock_id": [8, "WH/Stock"]}]
        if model == "stock.quant" and method == "search_read":
            return [{"id": 1, "quantity": 4.0}]
        if model == "stock.quant" and method == "action_apply_inventory":
            return True
        return []
    return fake_odoo


def test_id_path_uses_product_id_without_resolving(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []
    monkeypatch.setattr(server, "odoo", _base_stock_odoo(calls))
    out = server.inventory_adjustment(new_qty=10, product_id=552)
    assert "10" in out or "Đã" in out
    assert ("product.product", "read") in calls
    assert ("product.product", "search_read") not in calls


def test_id_path_unknown_product_id(monkeypatch):
    server = _reload_server(monkeypatch)
    monkeypatch.setattr(server, "odoo", lambda *a, **k: [])
    out = server.inventory_adjustment(new_qty=5, product_id=999)
    assert "999" in out and "Không tìm thấy" in out


def test_name_path_still_works(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []

    def fake_odoo(model, method, args, kwargs=None, **kw):
        calls.append((model, method))
        if model == "product.product" and method == "search_read":
            return [{"id": 552, "name": "Tủ", "default_code": "TU01", "list_price": 1.0}]
        if model == "stock.warehouse":
            return [{"lot_stock_id": [8, "WH/Stock"]}]
        if model == "stock.quant" and method == "search_read":
            return [{"id": 1, "quantity": 4.0}]
        if model == "stock.quant" and method == "action_apply_inventory":
            return True
        return []

    monkeypatch.setattr(server, "odoo", fake_odoo)
    out = server.inventory_adjustment(new_qty=10, product_name="Tủ")
    assert ("product.product", "search_read") in calls
