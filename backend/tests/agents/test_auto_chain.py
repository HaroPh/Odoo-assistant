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


# ── continuation: queue consumption ──────────────────────────────────────────
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.continuation import make_write_continuation_node


def _cgraph():
    g = StateGraph(ERPAgentState)
    g.add_node("write_continuation", make_write_continuation_node())
    g.set_entry_point("write_continuation")
    g.add_edge("write_continuation", END)
    return g.compile(checkpointer=MemorySaver())


def _lw(tool="create_quotation", ok=True, ref="S00031", res_id=42):
    return {"tool": tool, "ok": ok, "ref": ref, "model": "sale.order",
            "res_id": res_id, "state": "draft",
            "display": "Đã tạo báo giá S00031 (nháp) cho Azure."}


def _cstate(lw, queue):
    return {"messages": [], "intent": "erp_write", "confirmed": True,
            "pending_action": {"tool": "x"}, "last_write": lw,
            "auto_chain": queue}


@pytest.mark.asyncio
async def test_auto_proceed_no_interrupt():
    res = await _cgraph().ainvoke(_cstate(_lw(), ["confirm_sale_order"]),
                                  {"configurable": {"thread_id": "a1"}})
    assert "__interrupt__" not in res
    assert res["pending_action"] == {"tool": "confirm_sale_order",
                                     "args": {"order_ref": "S00031"},
                                     "summary": "Xác nhận báo giá"}
    assert res["confirmed"] is True
    assert res["last_write"] is None
    assert res["auto_chain"] is None          # queue exhausted


@pytest.mark.asyncio
async def test_auto_proceed_keeps_rest_of_queue():
    res = await _cgraph().ainvoke(
        _cstate(_lw(), ["confirm_sale_order", "deliver_order"]),
        {"configurable": {"thread_id": "a2"}})
    assert "__interrupt__" not in res
    assert res["auto_chain"] == ["deliver_order"]


@pytest.mark.asyncio
async def test_head_mismatch_falls_back_to_menu():
    graph, cfg = _cgraph(), {"configurable": {"thread_id": "a3"}}
    res = await graph.ainvoke(_cstate(_lw(), ["deliver_order"]), cfg)
    itr = res["__interrupt__"][0].value       # menu, NOT auto-run of wrong step
    assert itr["kind"] == "next_action"
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert res["auto_chain"] is None


@pytest.mark.asyncio
async def test_failed_write_with_queue_warns_and_clears():
    res = await _cgraph().ainvoke(_cstate(_lw(ok=False), ["confirm_sale_order"]),
                                  {"configurable": {"thread_id": "a4"}})
    assert "__interrupt__" not in res
    assert "Chuỗi tự động dừng" in res["messages"][-1].content
    assert res["auto_chain"] is None and res["last_write"] is None


@pytest.mark.asyncio
async def test_offchain_tool_with_queue_warns():
    # vd nhánh flag của edit: write chạy OK nhưng tool không có NEXT_STEPS entry
    lw = _lw(tool="flag_order_for_review", ok=True)
    res = await _cgraph().ainvoke(_cstate(lw, ["confirm_sale_order"]),
                                  {"configurable": {"thread_id": "a5"}})
    assert "Chuỗi tự động dừng" in res["messages"][-1].content
    assert res["auto_chain"] is None


@pytest.mark.asyncio
async def test_cancel_with_queue_is_silent():
    # lw falsy = user đã hủy ở draft confirm — không write nào chạy → im lặng
    res = await _cgraph().ainvoke(_cstate(None, ["confirm_sale_order"]),
                                  {"configurable": {"thread_id": "a6"}})
    assert res["messages"] == []
    assert res["auto_chain"] is None


@pytest.mark.asyncio
async def test_every_branch_writes_auto_chain_key_direct_call():
    node = make_write_continuation_node()
    out = await node({"messages": [], "last_write": None, "auto_chain": None})
    assert "auto_chain" in out and out["auto_chain"] is None
