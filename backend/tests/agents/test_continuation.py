import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
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
async def test_menu_replaced_by_suggestion_no_interrupt():
    graph, cfg = _graph(), {"configurable": {"thread_id": "c3"}}
    res = await graph.ainvoke(_state(_lw()), cfg)
    assert "__interrupt__" not in res
    content = res["messages"][-1].content
    assert "Đã tạo báo giá S00031" in content
    assert "Xác nhận báo giá" in content
    assert "Tiếp theo bạn có muốn" not in content
    assert "•" not in content
    assert "Dừng" not in content
    assert res["pending_action"] is None
    assert res["confirmed"] is None


def test_route_after_continuation():
    assert _route_after_continuation(
        {"pending_action": {"tool": "confirm_sale_order"}, "confirmed": True}
    ) == "erp_write_executor"
    assert _route_after_continuation(
        {"pending_action": None, "confirmed": None}) == END
    assert _route_after_continuation(
        {"pending_action": {"tool": "x"}, "confirmed": None}) == END
