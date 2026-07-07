# backend/tests/agents/test_auto_chain.py
"""Auto-chain multi-step planner: expand_chain, planner chain_until,
continuation queue, chain_note in coordinator confirms."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest

from backend.src.agents.write_registry import expand_chain


# ── expand_chain (total function) ────────────────────────────────────────────

def test_expand_one_step_sale():
    assert expand_chain("create_quotation", "confirm_sale_order") == \
        [("confirm_sale_order", "Xác nhận báo giá")]


def test_expand_full_sale_chain_to_invoice():
    steps = expand_chain("create_quotation", "post_invoice")
    assert [t for t, _ in steps] == ["confirm_sale_order", "deliver_order",
                                     "create_invoice_from_order", "post_invoice"]


def test_expand_purchase_chain():
    steps = expand_chain("create_rfq", "receive_order")
    assert [t for t, _ in steps] == ["confirm_purchase_order", "receive_order"]


def test_expand_edit_chain():
    assert expand_chain("update_quotation_lines", "confirm_sale_order") == \
        [("confirm_sale_order", "Xác nhận báo giá")]


def test_expand_missing_or_same_returns_none():
    assert expand_chain("create_quotation", None) is None
    assert expand_chain("create_quotation", "") is None
    assert expand_chain("create_quotation", "create_quotation") is None


def test_expand_unknown_or_unreachable_returns_none():
    assert expand_chain("create_quotation", "frobnicate") is None
    assert expand_chain("inventory_adjustment", "confirm_sale_order") is None
    # backwards: deliver comes AFTER confirm, can't walk back
    assert expand_chain("deliver_order", "confirm_sale_order") is None
    # cross-chain: sale start can't reach purchase tool
    assert expand_chain("create_quotation", "receive_order") is None


def test_expand_total_on_garbage():
    for a, b in [(None, None), (123, "x"), ("create_quotation", 123),
                 ({}, []), (None, "confirm_sale_order")]:
        assert expand_chain(a, b) is None
