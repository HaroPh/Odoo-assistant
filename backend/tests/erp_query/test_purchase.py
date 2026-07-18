import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query.gateway import Gateway
from backend.src.erp_query import purchase


class FakeTransport:
    def __init__(self, ret): self.ret = ret; self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs)); return self.ret


def _gw(rows): return Gateway(FakeTransport(rows))


def test_find_supplier_delegates_to_resolve():
    out = purchase.find_supplier("Acme", gw=_gw([(7, "Acme Co")]))
    assert out["data"]["matches"][0]["id"] == 7


def test_list_purchase_orders_envelope():
    rows = [{"name": "P00003", "partner_id": [7, "Acme"], "date_order": "2026-06-01",
             "state": "purchase", "amount_total": 5000.0}]
    gw = _gw(rows)
    out = purchase.list_purchase_orders(state="purchase", gw=gw)
    assert out["data"]["count"] == 1
    assert gw._t.calls[0][0] == "purchase.order"


def test_get_purchase_order_detail_includes_state():
    order_rows = [{"id": 9, "name": "P00009", "partner_id": [70, "ACME"],
                   "amount_total": 500000.0, "state": "draft"}]
    line_rows = [{"id": 201, "product_id": [553, "Bàn"], "product_qty": 4.0,
                  "price_unit": 125000.0, "price_subtotal": 500000.0}]

    class TwoCallTransport:
        def __init__(self): self.calls = []
        def call(self, model, method, args, kwargs):
            self.calls.append((model, method, args, kwargs))
            return order_rows if model == "purchase.order" else line_rows

    gw = Gateway(TwoCallTransport())
    out = purchase.get_purchase_order_detail("P00009", gw=gw)
    assert out["data"]["order"]["state"] == "draft"
    assert out["data"]["lines"][0]["id"] == 201
    order_call = next(c for c in gw._t.calls if c[0] == "purchase.order")
    assert "state" in order_call[3]["fields"]
    line_call = next(c for c in gw._t.calls if c[0] == "purchase.order.line")
    assert "id" in line_call[3]["fields"]


def test_list_suppliers_envelope():
    rows = [{"name": "Acme Co", "email": "a@acme.com", "phone": "123", "city": "HN"}]
    gw = _gw(rows)
    out = purchase.list_suppliers(gw=gw)
    assert out["data"]["count"] == 1
    assert gw._t.calls[0][0] == "res.partner"


def test_list_suppliers_empty():
    out = purchase.list_suppliers(gw=_gw([]))
    assert out["data"]["count"] == 0
    assert "Chưa có nhà cung cấp" in out["display"]


class MultiModelTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs))
        return self.responses.get((model, method), [])


def test_get_product_suppliers_not_found():
    from backend.src.erp_query.gateway import Gateway
    t = MultiModelTransport({("product.product", "name_search"): []})
    out = purchase.get_product_suppliers("Không tồn tại XYZ", gw=Gateway(t))
    assert out["status"] == "error"
    assert "Không tìm thấy" in out["display"]


def test_get_product_suppliers_declared_and_history():
    from backend.src.erp_query.gateway import Gateway
    t = MultiModelTransport({
        ("product.product", "name_search"): [(20, "[E-COM07] Large Cabinet")],
        ("product.product", "search_read"): [{"id": 20, "product_tmpl_id": [11, "tmpl"]}],
        ("product.supplierinfo", "search_read"): [
            {"partner_id": [11, "Ready Mat"], "price": 785.0, "min_qty": 3.0, "delay": 3}],
        ("purchase.order.line", "search_read"): [
            {"partner_id": [11, "Ready Mat"]}, {"partner_id": [10, "Gemini Furniture"]}],
    })
    out = purchase.get_product_suppliers("Large Cabinet", gw=Gateway(t))
    assert out["status"] == "success"
    assert out["data"]["history_partners"] == ["Ready Mat", "Gemini Furniture"]
    assert "Ready Mat" in out["display"] and "785" in out["display"]


def test_get_product_suppliers_no_declared_no_history():
    from backend.src.erp_query.gateway import Gateway
    t = MultiModelTransport({
        ("product.product", "name_search"): [(20, "X")],
        ("product.product", "search_read"): [{"id": 20, "product_tmpl_id": [11, "tmpl"]}],
        ("product.supplierinfo", "search_read"): [],
        ("purchase.order.line", "search_read"): [],
    })
    out = purchase.get_product_suppliers("X", gw=Gateway(t))
    assert "chưa có" in out["display"].lower()
    assert "chưa từng nhập" in out["display"].lower()


def test_get_supplier_detail_happy_path():
    from backend.src.erp_query.gateway import Gateway
    t = MultiModelTransport({
        ("res.partner", "name_search"): [(62, "Công ty CP ABC")],
        ("res.partner", "search_read"): [{
            "id": 62, "name": "Công ty CP ABC", "email": False, "phone": False,
            "vat": False, "street": False, "city": False, "bank_ids": [],
            "property_supplier_payment_term_id": False}],
        ("purchase.order", "search_read"): [{"id": 1}, {"id": 2}],
    })
    out = purchase.get_supplier_detail("ABC", gw=Gateway(t))
    assert out["status"] == "success"
    assert out["data"]["po_count"] == 2
    assert "—" in out["display"]


def test_get_supplier_detail_ambiguous():
    # NOTE: fixture deviates from task-3-brief.md — see task-3-report.md "Deviations".
    # resolve_entity's exact-match rule auto-picks a single verbatim name match even
    # among several candidates, so neither candidate name here may equal the query
    # exactly or this stops being an ambiguous case.
    from backend.src.erp_query.gateway import Gateway
    t = MultiModelTransport({
        ("res.partner", "name_search"): [(1, "Công ty A Miền Bắc"), (2, "Công ty A Miền Nam")],
    })
    out = purchase.get_supplier_detail("Công ty A", gw=Gateway(t))
    assert out["status"] == "error"
    assert "nhiều" in out["display"].lower()


def test_get_supplier_detail_not_found():
    from backend.src.erp_query.gateway import Gateway
    t = MultiModelTransport({("res.partner", "name_search"): []})
    out = purchase.get_supplier_detail("Không tồn tại", gw=Gateway(t))
    assert out["status"] == "error"
