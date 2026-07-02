import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
from backend.src.agents.continuation import (make_write_continuation_node,
                                             _route_after_continuation)


def _graph():
    g = StateGraph(ERPAgentState)
    g.add_node("write_continuation", make_write_continuation_node())
    g.set_entry_point("write_continuation")
    g.add_edge("write_continuation", END)
    return g.compile(checkpointer=MemorySaver())


def _lw(tool="create_quotation", ok=True, ref="S00031", res_id=42):
    return {"tool": tool, "ok": ok, "ref": ref, "model": "sale.order",
            "res_id": res_id, "state": "draft",
            "display": "Đã tạo báo giá S00031 (nháp) cho Azure."}


def _state(lw):
    return {"messages": [], "intent": "erp_write", "confirmed": True,
            "pending_action": {"tool": "x"}, "last_write": lw}


@pytest.mark.asyncio
async def test_no_last_write_clears_and_ends():
    res = await _graph().ainvoke(_state(None),
                                 {"configurable": {"thread_id": "c1"}})
    assert "__interrupt__" not in res
    assert res["pending_action"] is None and res["confirmed"] is None
    assert res["last_write"] is None
    assert res["messages"] == []          # terminal adds no message


@pytest.mark.asyncio
async def test_ok_false_or_unknown_tool_no_menu():
    for lw in (_lw(ok=False), _lw(tool="inventory_adjustment")):
        res = await _graph().ainvoke(_state(lw),
                                     {"configurable": {"thread_id": f"c-{lw['tool']}-{lw['ok']}"}})
        assert "__interrupt__" not in res
        assert res["last_write"] is None


@pytest.mark.asyncio
async def test_menu_embeds_display_and_next_label():
    graph, cfg = _graph(), {"configurable": {"thread_id": "c3"}}
    res = await graph.ainvoke(_state(_lw()), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "next_action"
    assert "Đã tạo báo giá S00031" in itr["question"]
    assert "Xác nhận báo giá" in itr["question"] and "Dừng" in itr["question"]
    assert itr["options"] == [{"id": True, "name": "Xác nhận báo giá"},
                              {"id": False, "name": "Dừng"}]
    assert "expires_at" in itr


@pytest.mark.asyncio
async def test_proceed_loads_next_action():
    graph, cfg = _graph(), {"configurable": {"thread_id": "c4"}}
    await graph.ainvoke(_state(_lw()), cfg)
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert res["pending_action"] == {"tool": "confirm_sale_order",
                                     "args": {"order_ref": "S00031"},
                                     "summary": "Xác nhận báo giá"}
    assert res["confirmed"] is True
    assert res["last_write"] is None      # invariant: cleared on every branch


@pytest.mark.asyncio
async def test_stop_adds_message_and_clears():
    graph, cfg = _graph(), {"configurable": {"thread_id": "c5"}}
    await graph.ainvoke(_state(_lw()), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert res["messages"][-1].content == "Đã dừng tại đây."
    assert res["pending_action"] is None and res["confirmed"] is None
    assert res["last_write"] is None


def test_route_after_continuation():
    assert _route_after_continuation(
        {"pending_action": {"tool": "confirm_sale_order"}, "confirmed": True}
    ) == "erp_write_executor"
    assert _route_after_continuation(
        {"pending_action": None, "confirmed": None}) == END
    assert _route_after_continuation(
        {"pending_action": {"tool": "x"}, "confirmed": None}) == END
