import server


def patch_odoo(monkeypatch, by_model):
    """Patch server.odoo to return by_model[model] (default []). Returns call log."""
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method, "args": args, "kwargs": kwargs})
        return by_model.get(model, [])

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


def fn(name):
    """Raw function behind the @mcp.tool() wrapper."""
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _models(calls):
    return [c["model"] for c in calls]


# ── get_customer_invoices ─────────────────────────────────────────────────────

def test_customer_invoices_happy(monkeypatch):
    rows = [{"name": "INV/2026/0001", "partner_id": [1, "Acme"],
             "invoice_date": "2026-06-01", "invoice_date_due": "2026-06-30",
             "amount_total": 1000.0, "amount_residual": 400.0,
             "payment_state": "partial"}]
    calls = patch_odoo(monkeypatch, {"account.move": rows})
    out = fn("get_customer_invoices")()
    assert "account.move" in _models(calls)
    domain = calls[0]["args"][0]
    assert ["move_type", "=", "out_invoice"] in domain
    assert "INV/2026/0001" in out and "Acme" in out and "400" in out


def test_customer_invoices_empty(monkeypatch):
    patch_odoo(monkeypatch, {"account.move": []})
    assert "Không" in fn("get_customer_invoices")()


def test_vendor_bills_filters_in_invoice(monkeypatch):
    calls = patch_odoo(monkeypatch, {"account.move": []})
    fn("get_vendor_bills")()
    assert ["move_type", "=", "in_invoice"] in calls[0]["args"][0]


def test_overdue_invoices_filters_unpaid_and_due(monkeypatch):
    rows = [{"name": "INV/2026/0009", "partner_id": [2, "Beta"],
             "invoice_date_due": "2026-01-01", "amount_total": 500.0,
             "amount_residual": 500.0}]
    calls = patch_odoo(monkeypatch, {"account.move": rows})
    out = fn("get_overdue_invoices")()
    domain = calls[0]["args"][0]
    assert ["payment_state", "in", ["not_paid", "partial"]] in domain
    assert any(c[0] == "invoice_date_due" and c[1] == "<" for c in domain)
    assert "INV/2026/0009" in out


# ── pickings ──────────────────────────────────────────────────────────────────

_PICKING_ROW = [{"name": "WH/OUT/0001", "partner_id": [1, "Acme"],
                 "scheduled_date": "2026-06-20 09:00:00", "state": "assigned",
                 "origin": "S00001"}]


def test_deliveries_filters_outgoing(monkeypatch):
    calls = patch_odoo(monkeypatch, {"stock.picking": _PICKING_ROW})
    out = fn("get_deliveries")()
    assert ["picking_type_code", "=", "outgoing"] in calls[0]["args"][0]
    assert "WH/OUT/0001" in out and "Acme" in out and "S00001" in out


def test_receipts_filters_incoming(monkeypatch):
    calls = patch_odoo(monkeypatch, {"stock.picking": []})
    out = fn("get_receipts")()
    assert ["picking_type_code", "=", "incoming"] in calls[0]["args"][0]
    assert "Không" in out


def test_internal_transfers_filters_internal(monkeypatch):
    calls = patch_odoo(monkeypatch, {"stock.picking": []})
    fn("get_internal_transfers")()
    assert ["picking_type_code", "=", "internal"] in calls[0]["args"][0]


def test_deliveries_state_filter_applied(monkeypatch):
    calls = patch_odoo(monkeypatch, {"stock.picking": []})
    fn("get_deliveries")(state="done")
    assert ["state", "=", "done"] in calls[0]["args"][0]


# ── lots + products ───────────────────────────────────────────────────────────

def test_search_lots_happy(monkeypatch):
    rows = [{"name": "LOT0001", "product_id": [3, "Paracetamol"], "product_qty": 120.0}]
    calls = patch_odoo(monkeypatch, {"stock.lot": rows})
    out = fn("search_lots")(product_name="Para")
    assert calls[0]["model"] == "stock.lot"
    assert ["product_id.name", "ilike", "Para"] in calls[0]["args"][0]
    assert "LOT0001" in out and "120" in out


def test_search_lots_empty(monkeypatch):
    patch_odoo(monkeypatch, {"stock.lot": []})
    assert "Không" in fn("search_lots")()


def test_search_products_or_domain_on_name_and_code(monkeypatch):
    rows = [{"name": "Office Chair", "default_code": "FURN-01",
             "list_price": 120.0, "standard_price": 70.0,
             "qty_available": 15.0, "uom_id": [1, "Units"]}]
    calls = patch_odoo(monkeypatch, {"product.product": rows})
    out = fn("search_products")(name="chair")
    domain = calls[0]["args"][0]
    assert domain[0] == "|"
    assert ["name", "ilike", "chair"] in domain
    assert ["default_code", "ilike", "chair"] in domain
    assert "Office Chair" in out and "FURN-01" in out


def test_search_products_no_filter_empty_domain(monkeypatch):
    calls = patch_odoo(monkeypatch, {"product.product": []})
    fn("search_products")()
    assert calls[0]["args"][0] == []


# ── order detail ──────────────────────────────────────────────────────────────

def test_sale_detail_not_found(monkeypatch):
    patch_odoo(monkeypatch, {"sale.order": []})
    assert "không tìm thấy" in fn("get_sale_order_detail")("S99999").lower()


def test_sale_detail_ambiguous(monkeypatch):
    patch_odoo(monkeypatch, {"sale.order": [
        {"id": 1, "name": "S001", "partner_id": [1, "A"], "amount_total": 1.0},
        {"id": 2, "name": "S001", "partner_id": [1, "A"], "amount_total": 2.0}]})
    assert "nhiều" in fn("get_sale_order_detail")("S001").lower()


def test_sale_detail_lists_lines(monkeypatch):
    order = [{"id": 7, "name": "S00014", "partner_id": [4, "Ready Mat"],
              "amount_total": 900.0}]
    lines = [{"product_id": [9, "Bàn gỗ"], "product_uom_qty": 3.0,
              "price_unit": 300.0, "price_subtotal": 900.0}]
    calls = patch_odoo(monkeypatch, {"sale.order": order, "sale.order.line": lines})
    out = fn("get_sale_order_detail")("S00014")
    assert "S00014" in out and "Ready Mat" in out
    assert "Bàn gỗ" in out and "900" in out
    line_call = [c for c in calls if c["model"] == "sale.order.line"][0]
    assert ["order_id", "=", 7] in line_call["args"][0]


def test_purchase_detail_lists_lines(monkeypatch):
    order = [{"id": 5, "name": "P00003", "partner_id": [2, "Vendor"],
              "amount_total": 500.0}]
    lines = [{"product_id": [8, "Ốc vít"], "product_qty": 100.0,
              "price_unit": 5.0, "price_subtotal": 500.0}]
    calls = patch_odoo(monkeypatch, {"purchase.order": order,
                                     "purchase.order.line": lines})
    out = fn("get_purchase_order_detail")("P00003")
    assert "P00003" in out and "Ốc vít" in out
    line_call = [c for c in calls if c["model"] == "purchase.order.line"][0]
    assert ["order_id", "=", 5] in line_call["args"][0]


# ── crm ───────────────────────────────────────────────────────────────────────

def test_search_leads_happy(monkeypatch):
    rows = [{"name": "Cơ hội A", "contact_name": "Anh B", "email_from": "b@x.com",
             "stage_id": [2, "Qualified"], "expected_revenue": 5000.0,
             "probability": 40.0, "user_id": [3, "NV Sales"], "type": "opportunity"}]
    calls = patch_odoo(monkeypatch, {"crm.lead": rows})
    out = fn("search_leads")(type="opportunity")
    assert calls[0]["model"] == "crm.lead"
    assert ["type", "=", "opportunity"] in calls[0]["args"][0]
    assert "Cơ hội A" in out and "Qualified" in out and "5,000" in out


def test_search_leads_salesperson_filter(monkeypatch):
    calls = patch_odoo(monkeypatch, {"crm.lead": []})
    out = fn("search_leads")(salesperson="Joel")
    assert ["user_id.name", "ilike", "Joel"] in calls[0]["args"][0]
    assert "Không" in out


# ── manufacturing ─────────────────────────────────────────────────────────────

def test_manufacturing_orders_happy(monkeypatch):
    rows = [{"name": "MO/0001", "product_id": [5, "Thành phẩm X"],
             "product_qty": 10.0, "state": "confirmed",
             "date_start": "2026-06-21 08:00:00"}]
    calls = patch_odoo(monkeypatch, {"mrp.production": rows})
    out = fn("get_manufacturing_orders")(state="confirmed")
    assert calls[0]["model"] == "mrp.production"
    assert ["state", "=", "confirmed"] in calls[0]["args"][0]
    # uses date_start, not date_planned_start
    assert "date_start" in calls[0]["kwargs"]["fields"]
    assert "MO/0001" in out and "Thành phẩm X" in out


def test_bom_not_found(monkeypatch):
    patch_odoo(monkeypatch, {"mrp.bom": []})
    assert "không tìm thấy" in fn("get_bom")("Khong Co").lower()


def test_bom_lists_components(monkeypatch):
    boms = [{"id": 3, "code": "BOM-X", "product_tmpl_id": [5, "Thành phẩm X"],
             "product_qty": 1.0}]
    comps = [{"product_id": [6, "Linh kiện B"], "product_qty": 2.0},
             {"product_id": [7, "Linh kiện C"], "product_qty": 1.0}]
    calls = patch_odoo(monkeypatch, {"mrp.bom": boms, "mrp.bom.line": comps})
    out = fn("get_bom")("Thành phẩm X")
    assert "Thành phẩm X" in out and "Linh kiện B" in out and "Linh kiện C" in out
    line_call = [c for c in calls if c["model"] == "mrp.bom.line"][0]
    assert ["bom_id", "=", 3] in line_call["args"][0]
