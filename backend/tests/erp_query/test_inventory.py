import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query.gateway import Gateway
from backend.src.erp_query import inventory


class FakeTransport:
    def __init__(self, ret): self.ret = ret; self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs)); return self.ret


def _gw(rows): return Gateway(FakeTransport(rows))


def test_find_product_delegates_to_resolve():
    out = inventory.find_product("Tủ", gw=_gw([(552, "Tủ lớn [TU01]")]))
    assert out["data"]["matches"][0]["id"] == 552


def test_get_stock_builds_internal_domain_and_envelope():
    rows = [{"product_id": [552, "Tủ lớn"], "location_id": [8, "WH/Stock"],
             "quantity": 10.0, "reserved_quantity": 2.0, "available_quantity": 8.0,
             "product_uom_id": [1, "Cái"]}]
    gw = _gw(rows)
    out = inventory.get_stock(product="Tủ", gw=gw)
    assert out["data"]["count"] == 1
    model, method, args, kwargs = gw._t.calls[0]
    assert model == "stock.quant"
    assert ["location_id.usage", "=", "internal"] in args[0]
