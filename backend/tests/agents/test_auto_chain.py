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
                 ({}, []), (None, "confirm_sale_order"),
                 ({}, "confirm_sale_order"), ([1, 2], "confirm_sale_order"),
                 (123, "confirm_sale_order")]:
        assert expand_chain(a, b) is None


# ── planner: chain_until → auto_chain + chain_note ───────────────────────────
import json
from unittest.mock import MagicMock
from langchain_core.messages import HumanMessage

from backend.src.agents.state import ERPAgentState
from backend.tests.conftest import make_mock_llm
import backend.src.agents.nodes as nodes_mod
from backend.src.agents.nodes import make_erp_write_planner_node


def _pstate(text):
    return ERPAgentState(messages=[HumanMessage(content=text)],
                         intent="erp_write", pending_action=None, confirmed=None)


def _mk_llm(payload):
    return make_mock_llm(json.dumps(payload, ensure_ascii=False))


@pytest.mark.asyncio
async def test_planner_valid_chain_sets_auto_chain_and_note(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    llm = _mk_llm({"tool": "create_quotation",
                   "args": {"partner_name": "Azur",
                            "lines": [{"product": "Tủ", "qty": 2}]},
                   "summary": "Tạo báo giá và xác nhận",
                   "chain_until": "confirm_sale_order"})
    out = await make_erp_write_planner_node(llm)(
        _pstate("tạo báo giá cho Azur, 2 Tủ rồi xác nhận luôn"))
    assert out["auto_chain"] == ["confirm_sale_order"]
    assert out["pending_action"]["chain_note"] == \
        "\n\nSau đó tự động: Xác nhận báo giá"


@pytest.mark.asyncio
async def test_planner_bogus_chain_falls_back_single_step(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    llm = _mk_llm({"tool": "create_quotation",
                   "args": {"partner_name": "Azur", "lines": []},
                   "summary": "Tạo báo giá", "chain_until": "frobnicate"})
    out = await make_erp_write_planner_node(llm)(_pstate("tạo báo giá"))
    assert out["auto_chain"] is None
    assert "chain_note" not in out["pending_action"]


@pytest.mark.asyncio
async def test_planner_noncoordinated_chain_note_in_confirm(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    captured = {}
    monkeypatch.setattr(nodes_mod, "_interrupt",
                        lambda p: captured.update(p) or True)
    llm = _mk_llm({"tool": "confirm_sale_order", "args": {"order_ref": "S00012"},
                   "summary": "Xác nhận S00012", "chain_until": "deliver_order"})
    out = await make_erp_write_planner_node(llm)(
        _pstate("xác nhận S00012 rồi giao hàng luôn"))
    assert "Sau đó tự động: Giao hàng" in captured["question"]
    assert out["auto_chain"] == ["deliver_order"]
    assert out["confirmed"] is True


@pytest.mark.asyncio
async def test_planner_gate_return_has_auto_chain_key(monkeypatch):
    monkeypatch.delenv("WRITE_ACTIONS_ENABLED", raising=False)
    out = await make_erp_write_planner_node(MagicMock())(_pstate("x"))
    assert "auto_chain" in out and out["auto_chain"] is None


@pytest.mark.asyncio
async def test_planner_non_json_return_has_auto_chain_key(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    out = await make_erp_write_planner_node(make_mock_llm("not json"))(_pstate("x"))
    assert "auto_chain" in out and out["auto_chain"] is None
