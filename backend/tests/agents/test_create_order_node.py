import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.create_order as co


def _fake_tool(recorder):
    t = MagicMock()
    t.name = "create_quotation"

    async def ainvoke(args):
        recorder["args"] = args
        return "Đã tạo báo giá S00099 cho Azur (1 dòng)."

    t.ainvoke = ainvoke
    return t


def _graph(node):
    g = StateGraph(ERPAgentState)
    g.add_node("create_order", node)
    g.set_entry_point("create_order")
    g.add_edge("create_order", END)
    return g.compile(checkpointer=MemorySaver())


def _state(lines):
    return {"messages": [], "intent": "erp_write", "confirmed": None,
            "pending_action": {"tool": "create_quotation",
                               "args": {"partner_name": "Azur", "lines": lines},
                               "summary": "Tạo báo giá"}}


def _ok(matches, needs):
    return {"status": "success", "data": {"matches": matches,
            "needs_disambiguation": needs}, "display": "x"}


@pytest.mark.asyncio
async def test_happy_path_creates_with_ids(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(co.sales, "find_customer",
                        lambda *a, **k: _ok([{"id": 41, "name": "Azur", "score": 1}], False))
    monkeypatch.setattr(co.inventory, "find_product",
                        lambda *a, **k: _ok([{"id": 552, "name": "Tủ", "score": 1}], False))
    monkeypatch.setattr(co.sales, "get_product_price",
                        lambda *a, **k: {"status": "success",
                                         "data": {"price": 100000.0}, "display": "x"})
    rec = {}
    node = co.make_create_order_node(MagicMock(), [_fake_tool(rec)])
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "t1"}}

    res = await graph.ainvoke(_state([{"product": "Tủ", "qty": 3}]), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm" and "Tủ" in itr["question"]

    res = await graph.ainvoke(Command(resume=True), cfg)
    assert "S00099" in res["messages"][-1].content
    assert rec["args"] == {"partner_id": 41, "lines": [{"product_id": 552, "qty": 3}]}


@pytest.mark.asyncio
async def test_ambiguous_customer_then_confirm(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(co.sales, "find_customer", lambda *a, **k: _ok(
        [{"id": 41, "name": "Azur Interior", "score": .6},
         {"id": 52, "name": "Azur Furniture", "score": .6}], True))
    monkeypatch.setattr(co.inventory, "find_product",
                        lambda *a, **k: _ok([{"id": 552, "name": "Tủ", "score": 1}], False))
    monkeypatch.setattr(co.sales, "get_product_price",
                        lambda *a, **k: {"status": "success",
                                         "data": {"price": 100000.0}, "display": "x"})
    rec = {}
    graph = _graph(co.make_create_order_node(MagicMock(), [_fake_tool(rec)]))
    cfg = {"configurable": {"thread_id": "t2"}}

    res = await graph.ainvoke(_state([{"product": "Tủ", "qty": 1}]), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "disambiguation"
    assert {o["id"] for o in itr["options"]} == {41, 52}

    res = await graph.ainvoke(Command(resume=52), cfg)   # pick Azur Furniture
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"

    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["partner_id"] == 52


@pytest.mark.asyncio
async def test_cancel_does_not_create(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(co.sales, "find_customer",
                        lambda *a, **k: _ok([{"id": 41, "name": "Azur", "score": 1}], False))
    monkeypatch.setattr(co.inventory, "find_product",
                        lambda *a, **k: _ok([{"id": 552, "name": "Tủ", "score": 1}], False))
    monkeypatch.setattr(co.sales, "get_product_price",
                        lambda *a, **k: {"status": "success",
                                         "data": {"price": 1.0}, "display": "x"})
    rec = {}
    graph = _graph(co.make_create_order_node(MagicMock(), [_fake_tool(rec)]))
    cfg = {"configurable": {"thread_id": "t3"}}
    await graph.ainvoke(_state([{"product": "Tủ", "qty": 1}]), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "hủy" in res["messages"][-1].content.lower()
    assert rec == {}


@pytest.mark.asyncio
async def test_zero_match_customer_is_terminal(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(co.sales, "find_customer", lambda *a, **k: _ok([], False))
    graph = _graph(co.make_create_order_node(MagicMock(), [_fake_tool({})]))
    cfg = {"configurable": {"thread_id": "t4"}}
    res = await graph.ainvoke(_state([{"product": "Tủ", "qty": 1}]), cfg)
    assert "__interrupt__" not in res
    assert "Không tìm thấy" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_write_disabled_gate(monkeypatch):
    monkeypatch.delenv("WRITE_ACTIONS_ENABLED", raising=False)
    graph = _graph(co.make_create_order_node(MagicMock(), [_fake_tool({})]))
    cfg = {"configurable": {"thread_id": "t5"}}
    res = await graph.ainvoke(_state([{"product": "Tủ", "qty": 1}]), cfg)
    assert "chưa được kích hoạt" in res["messages"][-1].content
