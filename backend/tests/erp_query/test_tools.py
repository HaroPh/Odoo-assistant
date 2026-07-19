import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
from backend.src.erp_query.tools import build_erp_query_tools


def test_build_tools_exposes_business_functions():
    names = {t.name for t in build_erp_query_tools()}
    assert {"find_customer", "find_product", "find_supplier", "list_sale_orders",
            "get_product_price", "get_stock", "list_invoices",
            "get_overdue_invoices"} <= names


def test_tool_returns_envelope_json(monkeypatch):
    import backend.src.erp_query.tools as tmod
    monkeypatch.setattr(tmod.inventory, "get_stock",
                        lambda *a, **kw: {"status": "success", "data": {"count": 0}, "display": "trống"})
    tool = next(t for t in build_erp_query_tools() if t.name == "get_stock")
    out = json.loads(tool.invoke({"product": "Tủ"}))
    assert out["status"] == "success" and out["display"] == "trống"


def test_build_tools_exposes_vendor_read_tools():
    names = {t.name for t in build_erp_query_tools()}
    assert {"list_suppliers", "get_product_suppliers",
            "get_supplier_detail"} <= names


def test_build_tools_exposes_list_crm_leads_only():
    names = {t.name for t in build_erp_query_tools()}
    assert "list_crm_leads" in names
    assert "find_lead" not in names          # nội bộ coordinator, không expose
    assert "find_lead_duplicates" not in names


def test_build_tools_exposes_list_reorder_needed():
    names = {t.name for t in build_erp_query_tools()}
    assert "list_reorder_needed" in names
