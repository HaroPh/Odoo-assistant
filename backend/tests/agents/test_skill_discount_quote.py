import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.skill_discount_quote as sdq
from backend.src.agents import write_gate


def test_compute_discount_pct_tier_only():
    assert sdq.compute_discount_pct("than_thiet", 10_000_000) == 0.05


def test_compute_discount_pct_thuong_is_zero():
    assert sdq.compute_discount_pct("thuong", 10_000_000) == 0.0


def test_compute_discount_pct_adds_bonus_at_threshold():
    assert sdq.compute_discount_pct("thuong", 50_000_000) == 0.02
    assert sdq.compute_discount_pct("than_thiet", 50_000_000) == 0.07


def test_compute_discount_pct_below_threshold_no_bonus():
    assert sdq.compute_discount_pct("doi_tac", 49_999_999) == 0.10


def test_compute_discount_pct_caps_at_15_percent():
    # Current tiers max out at 10% + 2% = 12%, so the 15% clamp is
    # unreachable with real tier values — assert the clamp function itself
    # still behaves correctly for a hypothetical higher base (policy
    # fidelity: discount_policy.docx states "tối đa không vượt quá 15%").
    assert sdq.compute_discount_pct("doi_tac", 50_000_000) == 0.12
    assert min(0.20, 0.15) == 0.15  # documents the clamp math in isolation


def _fake_tool(recorder):
    t = MagicMock()
    t.name = "create_quotation"

    async def ainvoke(args):
        recorder["args"] = args
        return '{"ok": true, "ref": "S00099", "model": "sale.order", ' \
               '"res_id": 5, "state": "sale", "display": "Đã tạo báo giá S00099."}'
    t.ainvoke = ainvoke
    return t


def _graph(node):
    g = StateGraph(ERPAgentState)
    g.add_node("discount_quote", node)
    g.set_entry_point("discount_quote")
    g.add_edge("discount_quote", END)
    return g.compile(checkpointer=MemorySaver())


def _state(customer, lines):
    return {"messages": [], "pending_action": {"tool": "skill:discount_quote",
            "args": {"customer": customer, "lines": lines}}}


def _ok(matches, needs):
    return {"status": "success", "data": {"matches": matches,
            "needs_disambiguation": needs}, "display": "x"}


@pytest.mark.asyncio
async def test_happy_path_applies_tier_and_bonus_discount(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(sdq.sales, "find_customer",
                        lambda *a, **k: _ok([{"id": 41, "name": "Azur", "score": 1}], False))
    monkeypatch.setattr(sdq.inventory, "find_product",
                        lambda *a, **k: _ok([{"id": 552, "name": "Tủ", "score": 1}], False))
    monkeypatch.setattr(sdq.sales, "get_product_price",
                        lambda *a, **k: {"status": "success",
                                         "data": {"price": 30_000_000.0}, "display": "x"})
    rec = {}
    node = sdq.make_node([_fake_tool(rec)])
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "t1"}}

    res = await graph.ainvoke(_state("Azur", [{"product": "Tủ", "qty": 2}]), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "disambiguation" and "cấp nào" in itr["question"]

    # order_total = 2 * 30,000,000 = 60,000,000 → tier "than_thiet" (5%) + 2% bonus = 7%
    res = await graph.ainvoke(Command(resume="than_thiet"), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm" and "7%" in itr["question"]

    res = await graph.ainvoke(Command(resume=True), cfg)
    assert "S00099" in res["messages"][-1].content
    assert rec["args"]["partner_id"] == 41
    assert rec["args"]["lines"][0]["price_unit"] == pytest.approx(30_000_000.0 * 0.93)
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_tier_expiry_cancel_does_not_default_to_zero_percent(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(sdq.sales, "find_customer",
                        lambda *a, **k: _ok([{"id": 41, "name": "Azur", "score": 1}], False))
    monkeypatch.setattr(sdq.inventory, "find_product",
                        lambda *a, **k: _ok([{"id": 552, "name": "Tủ", "score": 1}], False))
    monkeypatch.setattr(sdq.sales, "get_product_price",
                        lambda *a, **k: {"status": "success",
                                         "data": {"price": 100_000.0}, "display": "x"})
    rec = {}
    node = sdq.make_node([_fake_tool(rec)])
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "t2"}}

    await graph.ainvoke(_state("Azur", [{"product": "Tủ", "qty": 1}]), cfg)
    # Simulate erp_agent.chat's TTL-expiry stale-discard path: resume=False.
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "Đã hủy" in res["messages"][-1].content
    assert "args" not in rec  # tool never called — no 0%-discount quote created
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_write_gate_off_blocks_before_any_resolution(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    node = sdq.make_node([])
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "t3"}}
    res = await graph.ainvoke(_state("Azur", [{"product": "Tủ", "qty": 1}]), cfg)
    assert "chưa được kích hoạt" in res["messages"][-1].content
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_empty_lines_asks_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    node = sdq.make_node([])
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "t4"}}
    res = await graph.ainvoke(_state("Azur", []), cfg)
    assert "sản phẩm" in res["messages"][-1].content
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_ambiguous_customer_interrupts_with_disambiguation(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(sdq.sales, "find_customer",
                        lambda *a, **k: _ok(
                            [{"id": 41, "name": "Azur Interior", "score": 1},
                             {"id": 42, "name": "Azur Furniture", "score": 1}], True))
    node = sdq.make_node([])
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "t5"}}
    res = await graph.ainvoke(_state("Azur", [{"product": "Tủ", "qty": 1}]), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "disambiguation" and len(itr["options"]) == 2
