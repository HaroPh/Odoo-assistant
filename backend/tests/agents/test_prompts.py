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
    # the generalized order coordinator reads args["partner_name"] for purchase too,
    # so the RFQ contract must advertise partner_name (not supplier_name).
    assert "create_rfq(partner_name" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_inventory_adjustment():
    assert "inventory_adjustment" in WRITE_PLANNER_PROMPT
    assert "new_qty" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_create_invoice_from_order():
    assert "create_invoice_from_order(order_ref" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_deliver_order():
    assert "deliver_order(order_ref" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_receive_order():
    assert "receive_order(order_ref" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_create_bill_from_po():
    assert "create_bill_from_po(order_ref" in WRITE_PLANNER_PROMPT
