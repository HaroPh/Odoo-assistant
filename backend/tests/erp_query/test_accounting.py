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


class PartnerBalanceTransport:
    """Đường-2 call cùng model account.move (AR rồi AP) — phân biệt bằng
    domain (move_type), không phải thứ tự gọi."""
    def __init__(self, partner_rows, ar_rows, ap_rows):
        self.partner_rows = partner_rows
        self.ar_rows = ar_rows
        self.ap_rows = ap_rows
        self.calls = []

    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs))
        if model == "res.partner":
            return self.partner_rows
        domain = args[0]
        if ["move_type", "=", "out_invoice"] in domain:
            return self.ar_rows
        return self.ap_rows


def test_get_partner_balance_not_found():
    gw = Gateway(PartnerBalanceTransport([], [], []))
    out = accounting.get_partner_balance("Nobody", gw=gw)
    assert "Không tìm thấy" in out["display"]


def test_get_partner_balance_duplicate_name():
    partners = [{"id": 1, "name": "Acme"}, {"id": 2, "name": "Acme Corp"}]
    gw = Gateway(PartnerBalanceTransport(partners, [], []))
    out = accounting.get_partner_balance("Acme", gw=gw)
    assert "nhiều đối tác" in out["display"]


def test_get_partner_balance_ar_only():
    partners = [{"id": 15, "name": "Azure Interior"}]
    ar = [{"partner_id": [15, "Azure Interior"], "amount_residual": 66107.0}]
    gw = Gateway(PartnerBalanceTransport(partners, ar, []))
    out = accounting.get_partner_balance("Azure Interior", gw=gw)
    assert out["data"]["receivable"] == 66107.0
    assert out["data"]["payable"] == 0.0
    assert "phải thu" in out["display"]
    assert "phải trả" not in out["display"]


def test_get_partner_balance_ap_only():
    partners = [{"id": 10, "name": "Gemini Furniture"}]
    ap = [{"partner_id": [10, "Gemini Furniture"], "amount_residual": 1244.77}]
    gw = Gateway(PartnerBalanceTransport(partners, [], ap))
    out = accounting.get_partner_balance("Gemini Furniture", gw=gw)
    assert out["data"]["payable"] == 1244.77
    assert out["data"]["receivable"] == 0.0
    assert "phải trả" in out["display"]
    assert "phải thu" not in out["display"]


def test_get_partner_balance_both_sides_shown_not_netted():
    partners = [{"id": 20, "name": "Dual Corp"}]
    ar = [{"partner_id": [20, "Dual Corp"], "amount_residual": 500.0}]
    ap = [{"partner_id": [20, "Dual Corp"], "amount_residual": 200.0}]
    gw = Gateway(PartnerBalanceTransport(partners, ar, ap))
    out = accounting.get_partner_balance("Dual Corp", gw=gw)
    assert out["data"]["receivable"] == 500.0
    assert out["data"]["payable"] == 200.0
    assert "phải thu" in out["display"] and "phải trả" in out["display"]


def test_get_partner_balance_no_debt():
    partners = [{"id": 30, "name": "Clean Co"}]
    gw = Gateway(PartnerBalanceTransport(partners, [], []))
    out = accounting.get_partner_balance("Clean Co", gw=gw)
    assert "không còn công nợ" in out["display"]


def test_find_posted_invoice_not_found():
    gw = _gw([])
    out = accounting.find_posted_invoice("INV/2026/99999", gw=gw)
    assert out["status"] == "error"
    assert "Không tìm thấy" in out["display"]


def test_find_posted_invoice_not_yet_posted():
    gw = _gw([{"id": 68, "name": "INV/2026/00017", "state": "draft",
               "partner_id": [15, "Azure Interior"], "amount_total": 70.0}])
    out = accounting.find_posted_invoice("INV/2026/00017", gw=gw)
    assert out["status"] == "error"
    assert "chưa phát hành" in out["display"]


def test_find_posted_invoice_happy():
    gw = _gw([{"id": 68, "name": "INV/2026/00017", "state": "posted",
               "partner_id": [15, "Azure Interior"], "amount_total": 70.0}])
    out = accounting.find_posted_invoice("INV/2026/00017", gw=gw)
    assert out["status"] == "success"
    assert out["data"]["invoice"]["id"] == 68
