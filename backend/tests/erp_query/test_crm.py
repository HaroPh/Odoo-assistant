import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query.gateway import Gateway
from backend.src.erp_query import crm


class FakeTransport:
    def __init__(self, ret): self.ret = ret; self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs)); return self.ret


def _gw(rows): return Gateway(FakeTransport(rows))


def test_find_lead_delegates_to_resolve():
    out = crm.find_lead("PROBE", gw=_gw([(45, "PROBE-LEAD")]))
    assert out["data"]["matches"][0]["id"] == 45


def test_find_lead_duplicates_email_only_domain():
    gw = _gw([])
    crm.find_lead_duplicates(email="a@b.com", gw=gw)
    domain = gw._t.calls[0][2][0]
    assert ["email_from", "ilike", "a@b.com"] in domain
    assert "|" not in domain


def test_find_lead_duplicates_both_uses_or():
    gw = _gw([])
    crm.find_lead_duplicates(email="a@b.com", phone="0901", gw=gw)
    domain = gw._t.calls[0][2][0]
    assert domain[0] == "|"
    assert ["email_from", "ilike", "a@b.com"] in domain
    assert ["phone", "ilike", "0901"] in domain


def test_find_lead_duplicates_no_args_no_query():
    gw = _gw([])
    out = crm.find_lead_duplicates(gw=gw)
    assert out["data"]["rows"] == []
    assert gw._t.calls == []          # không chạm gateway khi không có gì để check


def test_list_crm_leads_kind_filter():
    gw = _gw([])
    crm.list_crm_leads(kind="lead", gw=gw)
    domain = gw._t.calls[0][2][0]
    assert ["type", "=", "lead"] in domain


def test_list_crm_leads_envelope_and_dash():
    rows = [{"name": "Lead A", "type": "lead", "contact_name": False,
            "partner_name": "Solar IT", "stage_id": [1, "New"],
            "user_id": False, "expected_revenue": 0.0}]
    out = crm.list_crm_leads(gw=_gw(rows))
    assert out["data"]["count"] == 1
    assert "—" in out["display"]          # field False hiển thị gạch, không lỗi


def test_list_crm_leads_empty():
    out = crm.list_crm_leads(gw=_gw([]))
    assert out["data"]["count"] == 0
    assert "chưa có" in out["display"].lower() or "không" in out["display"].lower()
