import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from pydantic import ValidationError
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


def test_build_tools_exposes_mrp_tools():
    names = {t.name for t in build_erp_query_tools()}
    assert {"get_bom_detail", "list_manufacturing_orders"} <= names


def test_build_tools_exposes_tier1_read_tools():
    names = {t.name for t in build_erp_query_tools()}
    assert {"list_late_deliveries", "check_po_matching",
            "list_po_mismatches", "get_partner_balance"} <= names


def test_extra_kwarg_rejected_reproduces_round6_bug():
    # Round 6's live-run finding, reproduced exactly: calling this tool with
    # only an unrecognized "ref" kwarg (no valid filter kwargs at all) used
    # to silently succeed with every real parameter defaulted, returning
    # every sale order unfiltered. It must now raise instead.
    tool = next(t for t in build_erp_query_tools() if t.name == "list_sale_orders")
    with pytest.raises(ValidationError):
        tool.invoke({"ref": "S00059"})


def test_valid_kwargs_still_work_after_fix(monkeypatch):
    import backend.src.erp_query.tools as tmod
    monkeypatch.setattr(tmod.sales, "list_sale_orders",
                        lambda *a, **kw: {"status": "success", "data": {"count": 0}, "display": "ok"})
    tool = next(t for t in build_erp_query_tools() if t.name == "list_sale_orders")
    out = json.loads(tool.invoke({"state": "sale"}))
    assert out["status"] == "success"


def test_extra_kwarg_rejected_on_another_tool():
    # Confirms the fix is applied generically (all 25 tools), not only to
    # list_sale_orders.
    tool = next(t for t in build_erp_query_tools() if t.name == "get_partner_balance")
    with pytest.raises(ValidationError):
        tool.invoke({"name": "Azure Interior", "bogus_field": "x"})


def test_zero_param_tool_extra_kwarg_is_a_known_langchain_limitation(monkeypatch):
    # Known, accepted limitation (spec §Findings #4): LangChain's
    # BaseTool._to_args_and_kwargs() skips Pydantic validation entirely when
    # args_schema has zero fields, so extra='forbid' has no effect on
    # zero-param tools. Harmless in practice (a zero-param tool has no
    # parameter a bad kwarg could override) — but if a future LangChain
    # upgrade changes this behavior, this test will FAIL and tell us, rather
    # than the gap staying silently uncovered.
    import backend.src.erp_query.tools as tmod
    monkeypatch.setattr(tmod.purchase, "list_po_mismatches",
                        lambda *a, **kw: {"status": "success", "data": {"count": 0}, "display": "ok"})
    tool = next(t for t in build_erp_query_tools() if t.name == "list_po_mismatches")
    out = json.loads(tool.invoke({"bogus_field": "x"}))  # does NOT raise
    assert out["status"] == "success"


def test_all_tools_configured_with_extra_forbid():
    for t in build_erp_query_tools():
        assert t.args_schema.model_config.get("extra") == "forbid", (
            f"{t.name} missing extra='forbid' on its args_schema")
