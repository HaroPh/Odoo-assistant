import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query.gateway import Gateway
from backend.src.erp_query import sales


class FakeTransport:
    def __init__(self, ret): self.ret = ret; self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs)); return self.ret


def _gw(rows): return Gateway(FakeTransport(rows))


def test_find_customer_delegates_to_resolve():
    out = sales.find_customer("Azur", gw=_gw([(41, "Azur Interior")]))
    assert out["data"]["matches"][0]["id"] == 41


def test_list_sale_orders_builds_domain_and_envelope():
    rows = [{"name": "S00042", "partner_id": [41, "Azur"], "date_order": "2026-06-01",
             "state": "sale", "amount_total": 320000.0, "delivery_status": "pending"}]
    gw = _gw(rows)
    out = sales.list_sale_orders(state="sale", customer="Azur", gw=gw)
    assert out["status"] == "success"
    assert out["data"]["count"] == 1
    assert out["data"]["rows"][0]["name"] == "S00042"
    assert "S00042" in out["display"]


def test_get_product_price_reads_list_price():
    # Odoo 19 has no context-computed `price` field on product.product, and the
    # read-only gateway can't call a pricelist method → list_price is the price.
    gw = _gw([{"id": 552, "name": "Tủ", "list_price": 320000.0}])
    out = sales.get_product_price(552, partner_id=41, qty=2, gw=gw)
    assert out["data"]["price"] == 320000.0
    assert out["data"]["product_id"] == 552
    fields = gw._t.calls[-1][3]["fields"]
    assert "list_price" in fields


def test_sales_summary_uses_read_group():
    gw = _gw([{"amount_total": 1000.0, "partner_id": [41, "Azur"]}])
    out = sales.sales_summary(period="month", gw=gw)
    assert out["status"] == "success"
    assert gw._t.calls[-1][1] == "read_group"


def test_get_sale_order_detail_includes_state():
    order_rows = [{"id": 7, "name": "S00007", "partner_id": [41, "Azur"],
                   "amount_total": 320000.0, "state": "draft"}]
    line_rows = [{"id": 101, "product_id": [552, "Tủ"], "product_uom_qty": 2.0,
                  "price_unit": 160000.0, "price_subtotal": 320000.0}]

    class TwoCallTransport:
        def __init__(self): self.calls = []
        def call(self, model, method, args, kwargs):
            self.calls.append((model, method, args, kwargs))
            return order_rows if model == "sale.order" else line_rows

    gw = Gateway(TwoCallTransport())
    out = sales.get_sale_order_detail("S00007", gw=gw)
    assert out["status"] == "success"
    assert out["data"]["order"]["state"] == "draft"
    assert out["data"]["lines"][0]["id"] == 101
    order_call = next(c for c in gw._t.calls if c[0] == "sale.order")
    assert "state" in order_call[3]["fields"]
    line_call = next(c for c in gw._t.calls if c[0] == "sale.order.line")
    assert "id" in line_call[3]["fields"]
