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
