import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def _reload_server(monkeypatch):
    for k in ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_PASSWORD"):
        monkeypatch.setenv(k, "x")
    import importlib, server
    importlib.reload(server)
    return server


def test_id_path_builds_po_from_ids_without_resolving(monkeypatch):
    server = _reload_server(monkeypatch)
    calls = []

    def fake_odoo(model, method, args, kwargs=None, **kw):
        calls.append((model, method, args, kwargs))
        if model == "res.partner" and method == "read":
            return [{"id": 7, "name": "Acme Co"}]
        if model == "purchase.order" and method == "create":
            return 3
        if model == "purchase.order" and method == "read":
            return [{"name": "P00003"}]
        return []

    monkeypatch.setattr(server, "odoo", fake_odoo)
    out = server.create_rfq(partner_id=7, lines=[{"product_id": 552, "qty": 5}])
    assert "P00003" in out
    create = next(c for c in calls if c[0] == "purchase.order" and c[1] == "create")
    line = create[2][0]["order_line"][0][2]
    assert line["product_id"] == 552 and line["product_qty"] == 5
    assert not any(c[0] == "product.product" for c in calls)


def test_id_path_unknown_partner_id(monkeypatch):
    server = _reload_server(monkeypatch)
    monkeypatch.setattr(server, "odoo", lambda *a, **k: [])
    out = server.create_rfq(partner_id=999, lines=[{"product_id": 1, "qty": 1}])
    assert "999" in out and "Không tìm thấy" in out


def test_name_path_still_works(monkeypatch):
    server = _reload_server(monkeypatch)

    def fake_odoo(model, method, args, kwargs=None, **kw):
        if model == "res.partner" and method == "search_read":
            return [{"id": 7, "name": "Acme Co", "email": "a@acme.vn"}]
        if model == "product.product" and method == "search_read":
            return [{"id": 552, "name": "Tủ", "default_code": "TU01", "list_price": 1.0}]
        if model == "purchase.order" and method == "create":
            return 4
        if model == "purchase.order" and method == "read":
            return [{"name": "P00004"}]
        return []

    monkeypatch.setattr(server, "odoo", fake_odoo)
    out = server.create_rfq(supplier_name="Acme", lines=[{"product": "Tủ", "qty": 2}])
    assert "P00004" in out
