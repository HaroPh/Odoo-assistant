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
    gw = Gateway(t)
    out = purchase.get_product_suppliers("Large Cabinet", gw=gw)
    assert out["status"] == "success"
    assert out["data"]["history_partners"] == ["Ready Mat", "Gemini Furniture"]
    assert "Ready Mat" in out["display"] and "785" in out["display"]
    # Lock in the real bug this suite exists to catch: product.supplierinfo has no
    # usable product_id on this Odoo instance (verified False on every live record) —
    # the query MUST filter by product_tmpl_id (11, from the mocked product.product
    # row), not by product_id (20).
    supplierinfo_call = next(c for c in gw._t.calls if c[0] == "product.supplierinfo")
    assert supplierinfo_call[2][0] == [["product_tmpl_id", "=", 11]]


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


def test_check_po_matching_not_found():
    out = purchase.check_po_matching("P99999", gw=_gw([]))
    assert "Không tìm thấy" in out["display"]


def test_check_po_matching_duplicate_name():
    orders = [{"id": 1, "name": "P00001", "partner_id": [1, "A"]},
              {"id": 2, "name": "P00001", "partner_id": [2, "B"]}]
    out = purchase.check_po_matching("P00001", gw=_gw(orders))
    assert "nhiều đơn mua" in out["display"]


def test_check_po_matching_flags_invoiced_over_received():
    order_rows = [{"id": 72, "name": "P00072", "partner_id": [11, "Ready Mat"]}]
    line_rows = [{"product_id": [21, "Storage Box"], "product_qty": 5.0,
                  "qty_received": 2.0, "qty_invoiced": 3.0}]

    class TwoCallTransport:
        def __init__(self): self.calls = []
        def call(self, model, method, args, kwargs):
            self.calls.append((model, method, args, kwargs))
            return order_rows if model == "purchase.order" else line_rows

    gw = Gateway(TwoCallTransport())
    out = purchase.check_po_matching("P00072", gw=gw)
    assert out["data"]["mismatch_count"] == 1
    assert "⚠" in out["display"]


def test_check_po_matching_partial_receipt_not_a_mismatch():
    # đang nhận dở (product_qty=5, mới nhận 2, CHƯA có hóa đơn) — KHÔNG lệch.
    order_rows = [{"id": 72, "name": "P00072", "partner_id": [11, "Ready Mat"]}]
    line_rows = [{"product_id": [21, "Storage Box"], "product_qty": 5.0,
                  "qty_received": 2.0, "qty_invoiced": 0.0}]

    class TwoCallTransport:
        def __init__(self): self.calls = []
        def call(self, model, method, args, kwargs):
            self.calls.append((model, method, args, kwargs))
            return order_rows if model == "purchase.order" else line_rows

    gw = Gateway(TwoCallTransport())
    out = purchase.check_po_matching("P00072", gw=gw)
    assert out["data"]["mismatch_count"] == 0
    assert "khớp" in out["display"]


def test_list_po_mismatches_domain_prefilters_invoiced():
    gw = _gw([])
    purchase.list_po_mismatches(gw=gw)
    model, method, args, kwargs = gw._t.calls[0]
    assert model == "purchase.order.line"
    domain = args[0]
    assert ["qty_invoiced", ">", 0] in domain


def test_list_po_mismatches_groups_by_po():
    lines = [{"order_id": [5, "P00005"], "product_id": [1, "A"],
              "product_qty": 2.0, "qty_received": 1.0, "qty_invoiced": 2.0},
             {"order_id": [5, "P00005"], "product_id": [2, "B"],
              "product_qty": 3.0, "qty_received": 1.0, "qty_invoiced": 3.0}]
    out = purchase.list_po_mismatches(gw=_gw(lines))
    assert out["data"]["count"] == 1          # 2 dòng lệch, CÙNG 1 PO


def test_list_po_mismatches_none_found():
    out = purchase.list_po_mismatches(gw=_gw([]))
    assert out["data"]["count"] == 0
    assert "Không có đơn mua nào" in out["display"]


def test_list_po_mismatches_capped_false_below_limit():
    """When fetched rows < 100, capped must be False and no warning in display."""
    lines = [{"order_id": [5, "P00005"], "product_id": [1, "A"],
              "product_qty": 2.0, "qty_received": 1.0, "qty_invoiced": 2.0}]
    out = purchase.list_po_mismatches(gw=_gw(lines))
    assert out["data"]["capped"] is False
    assert "có thể còn nhiều hơn" not in out["display"]
    assert out["data"]["count"] == 1


def test_list_po_mismatches_capped_true_at_limit():
    """When fetched rows == 100 (hit the cap), capped must be True and display warns."""
    # Create exactly 100 mismatched lines across different POs
    lines = []
    for i in range(100):
        po_id = 1000 + (i // 10)  # 10 POs with 10 lines each
        lines.append({
            "order_id": [po_id, f"P{po_id:05d}"],
            "product_id": [i + 1, f"Product_{i}"],
            "product_qty": 10.0,
            "qty_received": 5.0,
            "qty_invoiced": 8.0  # All are mismatches
        })
    out = purchase.list_po_mismatches(gw=_gw(lines))
    assert out["data"]["capped"] is True
    assert "có thể còn nhiều hơn — đã đạt giới hạn 100 dòng" in out["display"]
    # Should report 10 unique POs
    assert out["data"]["count"] == 10
