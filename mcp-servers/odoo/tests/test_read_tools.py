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
