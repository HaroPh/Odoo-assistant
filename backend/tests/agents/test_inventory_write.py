import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.inventory_write as iw
from backend.src.agents import write_gate


def _fake_tool(recorder):
    t = MagicMock()
    t.name = "inventory_adjustment"

    async def ainvoke(args):
        recorder["args"] = args
        return "Đã điều chỉnh tồn kho Tủ về 10 (trước: 4)."

    t.ainvoke = ainvoke
    return t


def _graph(node):
    g = StateGraph(ERPAgentState)
    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return g.compile(checkpointer=MemorySaver())


def _state(args):
    return {"messages": [], "intent": "erp_write", "confirmed": None,
            "pending_action": {"tool": "inventory_adjustment", "args": args,
                               "summary": "Điều chỉnh tồn kho"}}


def _ok(matches, needs):
    return {"status": "success", "data": {"matches": matches,
            "needs_disambiguation": needs}, "display": "x"}


@pytest.mark.asyncio
async def test_happy_path_sets_qty_by_id(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(iw.inventory, "find_product",
                        lambda *a, **k: _ok([{"id": 552, "name": "Tủ", "score": 1}], False))
    monkeypatch.setattr(iw.inventory, "get_stock",
                        lambda *a, **k: {"status": "success",
                                         "data": {"rows": [{"available_quantity": 4.0}], "count": 1},
                                         "display": "x"})
    rec = {}
    graph = _graph(iw.make_inventory_node([_fake_tool(rec)]))
    cfg = {"configurable": {"thread_id": "i1"}}
    res = await graph.ainvoke(_state({"product_name": "Tủ", "new_qty": 10,
                                      "location_name": "WH/Stock"}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"
    assert "Tủ" in itr["question"] and "10" in itr["question"] and "hiện tại: 4" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"] == {"product_id": 552, "new_qty": 10, "location_name": "WH/Stock"}


@pytest.mark.asyncio
async def test_ambiguous_product(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(iw.inventory, "find_product", lambda *a, **k: _ok(
        [{"id": 552, "name": "Tủ lớn", "score": .6},
         {"id": 553, "name": "Tủ nhỏ", "score": .6}], True))
    monkeypatch.setattr(iw.inventory, "get_stock", lambda *a, **k: {"status": "success",
                        "data": {"rows": [], "count": 0}, "display": "x"})
    rec = {}
    graph = _graph(iw.make_inventory_node([_fake_tool(rec)]))
    cfg = {"configurable": {"thread_id": "i2"}}
    res = await graph.ainvoke(_state({"product_name": "Tủ", "new_qty": 3}), cfg)
    assert res["__interrupt__"][0].value["kind"] == "disambiguation"
    res = await graph.ainvoke(Command(resume=553), cfg)
    assert res["__interrupt__"][0].value["kind"] == "confirm"
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["product_id"] == 553


@pytest.mark.asyncio
async def test_cancel(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(iw.inventory, "find_product",
                        lambda *a, **k: _ok([{"id": 552, "name": "Tủ", "score": 1}], False))
    monkeypatch.setattr(iw.inventory, "get_stock", lambda *a, **k: {"status": "success",
                        "data": {"rows": [], "count": 0}, "display": "x"})
    rec = {}
    graph = _graph(iw.make_inventory_node([_fake_tool(rec)]))
    cfg = {"configurable": {"thread_id": "i3"}}
    await graph.ainvoke(_state({"product_name": "Tủ", "new_qty": 3}), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "hủy" in res["messages"][-1].content.lower()
    assert rec == {}


@pytest.mark.asyncio
async def test_gate(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    graph = _graph(iw.make_inventory_node([_fake_tool({})]))
    cfg = {"configurable": {"thread_id": "i4"}}
    res = await graph.ainvoke(_state({"product_name": "Tủ", "new_qty": 3}), cfg)
    assert "chưa được kích hoạt" in res["messages"][-1].content
