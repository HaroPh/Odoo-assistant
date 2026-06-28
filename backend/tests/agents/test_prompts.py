import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.prompts import WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_confirm_sale_order_contract():
    # The planner must emit the exact tool name + arg key the executor accepts.
    assert "confirm_sale_order" in WRITE_PLANNER_PROMPT
    assert "order_ref" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_t2_contracts():
    assert "confirm_purchase_order" in WRITE_PLANNER_PROMPT
    assert "post_invoice" in WRITE_PLANNER_PROMPT
    assert "validate_picking" in WRITE_PLANNER_PROMPT
    assert "partner_name" in WRITE_PLANNER_PROMPT
    assert "picking_ref" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_post_invoice_disambiguators():
    assert "amount: float" in WRITE_PLANNER_PROMPT
    assert "invoice_date" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_create_quotation():
    assert "create_quotation" in WRITE_PLANNER_PROMPT
    assert "lines" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_create_rfq():
    assert "create_rfq" in WRITE_PLANNER_PROMPT
    assert "supplier_name" in WRITE_PLANNER_PROMPT
