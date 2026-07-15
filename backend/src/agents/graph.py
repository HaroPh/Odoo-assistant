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
from .write_registry import WRITE_COORDINATORS, COORDINATED_TOOLS
from .continuation import make_write_continuation_node, _route_after_continuation
from .models import llms_from_single
from .skills import SKILLS, match_skill, make_skill_extract_node, route_after_skill_extract
from . import skill_gate


def _route_by_intent(state: ERPAgentState) -> str:
    if skill_gate.skills_enabled():
        last_human = next((m.content for m in reversed(state["messages"])
                           if m.type == "human"), "")
        if match_skill(last_human):
            return "skill_extract"
    return state.get("intent") or "unknown"


def _route_after_write_planner(state: ERPAgentState) -> str:
    action = state.get("pending_action")
    if action is None:
        # Write locked or unparseable: planner already added a message → END
        return END
    tool = action.get("tool")
    if tool in COORDINATED_TOOLS:
        return WRITE_COORDINATORS[tool].node
    return "erp_write_executor"


def build_graph(llm, tools, checkpointer) -> object:
    # Nhận single-llm (test/back-compat: mọi role chung 1 model) HOẶC mapping
    # role→llm (production, từ make_llms()). Normalize về mapping.
    llms = llm if isinstance(llm, dict) else llms_from_single(llm)

    g = StateGraph(ERPAgentState)

    g.add_node("intent_router", make_intent_router_node(llms["router"]))
    g.add_node("erp_read", make_erp_read_node(llms["read"], build_erp_query_tools()))
    g.add_node("erp_write_planner", make_erp_write_planner_node(llms["planner"]))
    g.add_node("erp_write_executor", make_erp_write_executor_node(tools))
    g.add_node("rag", make_rag_node(llms["synthesis"]))
    g.add_node("mixed", make_fusion_node(llms["fusion"], build_erp_query_tools()))
    g.add_node("respond_unknown", make_respond_unknown_node(llms["chitchat"]))
    for spec in WRITE_COORDINATORS.values():
        g.add_node(spec.node, spec.build(llms["planner"], tools))
    g.add_node("write_continuation", make_write_continuation_node())
    g.add_node("skill_extract", make_skill_extract_node(llms["planner"]))
    for spec in SKILLS.values():
        g.add_node(spec.node, spec.build(tools))
        g.add_edge(spec.node, END)

    g.set_entry_point("intent_router")

    intent_targets = {
        "erp_read": "erp_read",
        "erp_write": "erp_write_planner",
        "rag": "rag",
        "mixed": "mixed",
        "unknown": "respond_unknown",
        "skill_extract": "skill_extract",
    }
    g.add_conditional_edges("intent_router", _route_by_intent, intent_targets)
    skill_targets = {END: END}
    skill_targets.update({spec.node: spec.node for spec in SKILLS.values()})
    g.add_conditional_edges("skill_extract", route_after_skill_extract, skill_targets)

    g.add_edge("erp_read", END)
    write_targets = {END: END, "erp_write_executor": "erp_write_executor"}
    write_targets.update({spec.node: spec.node for spec in WRITE_COORDINATORS.values()})
    g.add_conditional_edges("erp_write_planner", _route_after_write_planner, write_targets)
    g.add_edge("erp_write_executor", "write_continuation")
    for spec in WRITE_COORDINATORS.values():
        g.add_edge(spec.node, "write_continuation")
    g.add_conditional_edges("write_continuation", _route_after_continuation,
                            {"erp_write_executor": "erp_write_executor", END: END})
    g.add_edge("rag", END)
    g.add_edge("mixed", END)
    g.add_edge("respond_unknown", END)

    return g.compile(checkpointer=checkpointer)
