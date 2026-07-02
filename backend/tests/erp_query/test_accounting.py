import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query.gateway import Gateway
from backend.src.erp_query import accounting


class FakeTransport:
    def __init__(self, ret): self.ret = ret; self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs)); return self.ret


def _gw(rows): return Gateway(FakeTransport(rows))


def test_list_invoices_builds_move_type_domain():
    rows = [{"name": "INV/2026/0042", "partner_id": [41, "Azur"], "invoice_date": "2026-06-01",
             "amount_total": 320000.0, "amount_residual": 0.0, "payment_state": "paid"}]
    gw = _gw(rows)
    out = accounting.list_invoices("out_invoice", payment_state="paid", gw=gw)
    assert out["data"]["count"] == 1
    assert ["move_type", "=", "out_invoice"] in gw._t.calls[0][2][0]
    assert ["state", "=", "posted"] in gw._t.calls[0][2][0]


def test_get_overdue_invoices_domain():
    gw = _gw([])
    out = accounting.get_overdue_invoices(gw=gw)
    assert out["data"]["count"] == 0
    dom = gw._t.calls[0][2][0]
    assert ["payment_state", "in", ["not_paid", "partial"]] in dom
