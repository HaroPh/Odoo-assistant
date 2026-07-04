import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from langchain_core.messages import HumanMessage, RemoveMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.checkpoint.memory import MemorySaver
from backend.src.agents.state import ERPAgentState


@pytest.mark.asyncio
async def test_working_context_survives_message_reset_across_turns():
    # Pins the load-bearing LangGraph semantics: _invoke_fresh wipes ONLY the
    # messages channel; an unmentioned working_context key persists per-thread.
    captured = {}

    async def probe(state: ERPAgentState) -> dict:
        wc = state.get("working_context")
        if wc:
            captured["wc"] = wc
            return {}
        return {"working_context": {"ref": "S00040", "model": "sale.order",
                                    "display": "x"}}

    g = StateGraph(ERPAgentState)
    g.add_node("probe", probe)
    g.set_entry_point("probe")
    g.add_edge("probe", END)
    graph = g.compile(checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "wc-flow"}}

    await graph.ainvoke({"messages": [HumanMessage("tạo báo giá ...")]}, cfg)
    # Turn 2 mimics _invoke_fresh: full message-channel reset + new history.
    await graph.ainvoke({"messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES),
                                      HumanMessage("xác nhận đơn vừa tạo")]}, cfg)
    assert captured["wc"]["ref"] == "S00040"
