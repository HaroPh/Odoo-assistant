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


from backend.src.agents.prompts import render_working_context


def test_render_working_context_contains_ref_and_model_vi():
    text = render_working_context({"ref": "S00040", "model": "sale.order",
                                   "display": "Đã tạo báo giá S00040 (nháp)."})
    assert "S00040" in text and "đơn bán" in text


def test_render_working_context_instructs_explicit_wins():
    text = render_working_context({"ref": "P00015", "model": "purchase.order",
                                   "display": "x"})
    assert "đơn mua" in text
    assert "LUÔN dùng mã người dùng nêu" in text


def test_planner_prompt_advertises_register_payment():
    assert "register_payment(invoice_ref" in WRITE_PLANNER_PROMPT
    assert "journal" in WRITE_PLANNER_PROMPT


def test_planner_prompt_chain_description_ends_at_register_payment():
    assert "post_invoice → register_payment" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_crm_tools():
    assert "create_lead(name" in WRITE_PLANNER_PROMPT
    assert "convert_lead(lead_ref" in WRITE_PLANNER_PROMPT
    assert "log_activity(lead_ref" in WRITE_PLANNER_PROMPT


def test_planner_prompt_mentions_crm_chain():
    assert "create_lead → convert_lead" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_internal_transfer():
    assert "internal_transfer" in WRITE_PLANNER_PROMPT
    assert "from_location" in WRITE_PLANNER_PROMPT
    assert "to_location" in WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_scrap_product():
    assert "scrap_product" in WRITE_PLANNER_PROMPT
    assert "scrap_product(product_name" in WRITE_PLANNER_PROMPT
