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


# Live-verify (edit-draft-order slice) found the planner LLM self-censors and
# emits tool:"other" instead of update_quotation_lines/update_rfq_lines when
# conversation history shows the order is already confirmed — because the
# prompt originally said these tools are for orders "CHƯA xác nhận" (not yet
# confirmed), which the LLM takes literally as "don't call this on a confirmed
# order." That pre-empts edit_order.py's own confirmed-order fallback branch
# (Invariants #2/#3/#4), which is supposed to make that state-based decision
# itself. The prompt must tell the LLM to always route edit requests to these
# tools regardless of apparent order state.
def test_edit_tools_prompt_does_not_restrict_to_unconfirmed_orders():
    assert "CHƯA xác nhận" not in WRITE_PLANNER_PROMPT
    assert "chưa xác nhận" not in WRITE_PLANNER_PROMPT
    assert "kể cả nếu đơn đã xác nhận" in WRITE_PLANNER_PROMPT


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


def test_chain_until_documented_in_planner_prompt():
    assert "chain_until" in WRITE_PLANNER_PROMPT


def test_cloud_bound_prompts_have_no_think_suffix():
    # QĐ M2: router/evaluator/chitchat được phép chạy cloud (Gemini/Gemma) —
    # prompt của chúng không được chứa chỉ thị đặc thù Qwen '/no_think'.
    # (3 prompt local — SYSTEM_PROMPT, RAG_SYNTHESIS, FUSION — GIỮ /no_think.)
    from backend.src.agents.prompts import INTENT_ROUTER_PROMPT
    from backend.src.agents.confirmation import _LLM_PROMPT
    assert "/no_think" not in INTENT_ROUTER_PROMPT
    assert "/no_think" not in _LLM_PROMPT
    # chit-chat (respond_unknown) giờ CÓ persona system prompt cloud → cũng không /no_think.
    from backend.src.agents.prompts import CHITCHAT_PROMPT
    assert "/no_think" not in CHITCHAT_PROMPT
