import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.prompts import WRITE_PLANNER_PROMPT


# The generalized order coordinator (make_order_node) reads args["partner_name"] for
# BOTH sale and purchase. So the write-planner prompt must instruct the LLM to emit
# `partner_name` for both create_quotation and create_rfq — otherwise the RFQ flow
# dead-ends resolving an empty partner. Guards the planner↔coordinator seam.
def test_order_tools_emit_partner_name_not_supplier_name():
    assert "create_quotation(partner_name" in WRITE_PLANNER_PROMPT
    assert "create_rfq(partner_name" in WRITE_PLANNER_PROMPT
    assert "supplier_name" not in WRITE_PLANNER_PROMPT


def test_edit_tools_in_planner_prompt_with_changes_key():
    assert "update_quotation_lines(order_ref" in WRITE_PLANNER_PROMPT
    assert "update_rfq_lines(order_ref" in WRITE_PLANNER_PROMPT
    # planner emits `changes` (by-name), NOT `ops` (by-id — coordinator's job)
    assert "changes" in WRITE_PLANNER_PROMPT


def test_edit_tools_registered_as_coordinated():
    from backend.src.agents.write_registry import COORDINATED_TOOLS, WRITE_COORDINATORS, NEXT_STEPS
    assert "update_quotation_lines" in COORDINATED_TOOLS
    assert "update_rfq_lines" in COORDINATED_TOOLS
    assert WRITE_COORDINATORS["update_quotation_lines"].node == "edit_order"
    assert WRITE_COORDINATORS["update_rfq_lines"].node == "edit_rfq"
    # after editing a draft, suggest confirming it
    assert NEXT_STEPS["update_quotation_lines"].tool == "confirm_sale_order"
    assert NEXT_STEPS["update_rfq_lines"].tool == "confirm_purchase_order"
    # flag tool is terminal (no continuation)
    assert "flag_order_for_review" not in NEXT_STEPS
