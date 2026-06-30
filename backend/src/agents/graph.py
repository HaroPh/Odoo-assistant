# backend/src/agents/graph.py
from langgraph.graph import StateGraph, END

from .state import ERPAgentState
from ..erp_query.tools import build_erp_query_tools
from .nodes import (
    make_intent_router_node,
    make_erp_read_node,
    make_erp_write_planner_node,
    make_erp_write_executor_node,
    make_rag_node,
    make_respond_unknown_node,
)
from .fusion import make_fusion_node


def _route_by_intent(state: ERPAgentState) -> str:
    return state.get("intent") or "unknown"


def _route_after_write_planner(state: ERPAgentState) -> str:
    # If write is locked, planner added a message and pending_action is None → go to END
    if state.get("pending_action") is None:
        return END
    return "erp_write_executor"


def build_graph(llm, tools, checkpointer) -> object:
    g = StateGraph(ERPAgentState)

    g.add_node("intent_router", make_intent_router_node(llm))
    g.add_node("erp_read", make_erp_read_node(llm, build_erp_query_tools()))
    g.add_node("erp_write_planner", make_erp_write_planner_node(llm))
    g.add_node("erp_write_executor", make_erp_write_executor_node(tools))
    g.add_node("rag", make_rag_node(llm))
    g.add_node("mixed", make_fusion_node(llm, build_erp_query_tools()))
    g.add_node("respond_unknown", make_respond_unknown_node(llm))

    g.set_entry_point("intent_router")

    g.add_conditional_edges("intent_router", _route_by_intent, {
        "erp_read": "erp_read",
        "erp_write": "erp_write_planner",
        "rag": "rag",
        "mixed": "mixed",
        "unknown": "respond_unknown",
    })

    g.add_edge("erp_read", END)
    g.add_conditional_edges("erp_write_planner", _route_after_write_planner, {
        END: END,
        "erp_write_executor": "erp_write_executor",
    })
    g.add_edge("erp_write_executor", END)
    g.add_edge("rag", END)
    g.add_edge("mixed", END)
    g.add_edge("respond_unknown", END)

    return g.compile(checkpointer=checkpointer)
