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
            self.calls.append((model, method))
            return order_rows if model == "purchase.order" else line_rows

    gw = Gateway(TwoCallTransport())
    out = purchase.get_purchase_order_detail("P00009", gw=gw)
    assert out["data"]["order"]["state"] == "draft"
    assert out["data"]["lines"][0]["id"] == 201
