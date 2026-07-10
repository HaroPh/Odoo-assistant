import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.create_order as co
from backend.src.agents import write_gate


def _fake_tool(name, recorder):
    t = MagicMock()
    t.name = name

    async def ainvoke(args):
        recorder["args"] = args
        return f"Đã tạo RFQ P00003."

    t.ainvoke = ainvoke
    return t


def _graph(node):
    g = StateGraph(ERPAgentState)
    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return g.compile(checkpointer=MemorySaver())


def _state(lines):
    return {"messages": [], "intent": "erp_write", "confirmed": None,
            "pending_action": {"tool": "create_rfq",
                               "args": {"partner_name": "Acme", "lines": lines},
                               "summary": "Tạo RFQ"}}


def _ok(matches, needs):
    return {"status": "success", "data": {"matches": matches,
            "needs_disambiguation": needs}, "display": "x"}


@pytest.mark.asyncio
async def test_purchase_happy_path_no_price(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(co.purchase, "find_supplier",
                        lambda *a, **k: _ok([{"id": 7, "name": "Acme", "score": 1}], False))
    monkeypatch.setattr(co.inventory, "find_product",
                        lambda *a, **k: _ok([{"id": 552, "name": "Tủ", "score": 1}], False))
    # pricing must NOT be called on the purchase path
    monkeypatch.setattr(co.sales, "get_product_price",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("priced on purchase")))
    rec = {}
    node = co.make_order_node([_fake_tool("create_rfq", rec)], co.PURCHASE_CFG)
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "p1"}}

    res = await graph.ainvoke(_state([{"product": "Tủ", "qty": 5}]), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"
    assert "Đơn mua từ" in itr["question"] and "Tổng" not in itr["question"]

    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"] == {"partner_id": 7, "lines": [{"product_id": 552, "qty": 5}]}


@pytest.mark.asyncio
async def test_purchase_ambiguous_supplier(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(co.purchase, "find_supplier", lambda *a, **k: _ok(
        [{"id": 7, "name": "Acme Co", "score": .6},
         {"id": 8, "name": "Acme Ltd", "score": .6}], True))
    monkeypatch.setattr(co.inventory, "find_product",
                        lambda *a, **k: _ok([{"id": 552, "name": "Tủ", "score": 1}], False))
    rec = {}
    graph = _graph(co.make_order_node([_fake_tool("create_rfq", rec)], co.PURCHASE_CFG))
    cfg = {"configurable": {"thread_id": "p2"}}
    res = await graph.ainvoke(_state([{"product": "Tủ", "qty": 1}]), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "disambiguation" and {o["id"] for o in itr["options"]} == {7, 8}
    res = await graph.ainvoke(Command(resume=8), cfg)
    assert res["__interrupt__"][0].value["kind"] == "confirm"
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["partner_id"] == 8


@pytest.mark.asyncio
async def test_purchase_gate(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    graph = _graph(co.make_order_node([_fake_tool("create_rfq", {})], co.PURCHASE_CFG))
    cfg = {"configurable": {"thread_id": "p3"}}
    res = await graph.ainvoke(_state([{"product": "Tủ", "qty": 1}]), cfg)
    assert "chưa được kích hoạt" in res["messages"][-1].content
