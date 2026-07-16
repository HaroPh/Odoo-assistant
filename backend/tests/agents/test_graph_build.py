import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest

from langchain_core.messages import HumanMessage

from backend.src.agents.graph import build_graph


def test_build_graph_compiles_with_write_executor_factory():
    llm = MagicMock()
    tools = []  # executor factory must accept an empty tool list
    graph = build_graph(llm, tools, checkpointer=None)
    assert graph is not None
    # erp_write_executor must be a registered node
    assert "erp_write_executor" in graph.get_graph().nodes


def test_build_graph_includes_mixed_node():
    llm = MagicMock()
    graph = build_graph(llm, tools=[], checkpointer=None)
    assert "mixed" in graph.get_graph().nodes


def test_erp_read_uses_erp_query_tools():
    llm = MagicMock()
    graph = build_graph(llm, tools=[], checkpointer=None)
    assert "erp_read" in graph.get_graph().nodes


def test_mixed_node_built_with_erp_query_read_tools(monkeypatch):
    # fusion (mixed) must read ERP via the erp_query business tools, not the MCP
    # do-tools (which are write-only now). Spy on make_fusion_node's tool arg.
    import backend.src.agents.graph as graph_mod
    captured = {}
    real = graph_mod.make_fusion_node

    def spy(llm, tools):
        captured["names"] = [t.name for t in tools]
        return real(llm, tools)

    monkeypatch.setattr(graph_mod, "make_fusion_node", spy)
    graph_mod.build_graph(MagicMock(), tools=[], checkpointer=None)
    # erp_query read tools are present...
    assert {"list_sale_orders", "get_stock", "get_overdue_invoices"} <= set(captured["names"])
    # ...and no MCP write/do-tool leaks into fusion
    assert "post_invoice" not in captured["names"]
    assert "confirm_sale_order" not in captured["names"]


def test_route_after_planner_sends_create_quotation_to_coordinator():
    from backend.src.agents.graph import _route_after_write_planner
    from langgraph.graph import END
    assert _route_after_write_planner({"pending_action": None}) == END
    assert _route_after_write_planner(
        {"pending_action": {"tool": "create_quotation"}}) == "create_order"
    assert _route_after_write_planner(
        {"pending_action": {"tool": "confirm_sale_order"}}) == "erp_write_executor"


def test_build_graph_has_create_order_node():
    llm = MagicMock()
    graph = build_graph(llm, tools=[], checkpointer=None)
    assert "create_order" in graph.get_graph().nodes


def test_route_after_planner_maps_all_coordinated_writes():
    from backend.src.agents.graph import _route_after_write_planner
    from langgraph.graph import END
    assert _route_after_write_planner({"pending_action": None}) == END
    assert _route_after_write_planner(
        {"pending_action": {"tool": "create_quotation"}}) == "create_order"
    assert _route_after_write_planner(
        {"pending_action": {"tool": "create_rfq"}}) == "create_rfq"
    assert _route_after_write_planner(
        {"pending_action": {"tool": "inventory_adjustment"}}) == "inventory_adjust"
    assert _route_after_write_planner(
        {"pending_action": {"tool": "confirm_sale_order"}}) == "erp_write_executor"


def test_build_graph_registers_all_coordinator_nodes():
    llm = MagicMock()
    graph = build_graph(llm, tools=[], checkpointer=None)
    nodes = graph.get_graph().nodes
    assert {"create_order", "create_rfq", "inventory_adjust"} <= set(nodes)


def test_planner_returns_pending_for_each_coordinated_tool():
    from backend.src.agents.write_registry import COORDINATED_TOOLS
    assert {"create_quotation", "create_rfq", "inventory_adjustment"} <= COORDINATED_TOOLS


def test_build_graph_registers_write_continuation():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    assert "write_continuation" in graph.get_graph().nodes


def test_all_writes_route_through_continuation():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("erp_write_executor", "write_continuation") in edges
    for node in ("create_order", "create_rfq", "inventory_adjust"):
        assert (node, "write_continuation") in edges
    assert ("erp_write_executor", "__end__") not in edges


def test_continuation_loops_back_to_executor():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("write_continuation", "erp_write_executor") in edges
    assert ("write_continuation", "__end__") in edges


def test_build_graph_accepts_role_mapping(monkeypatch):
    # Previously this test asserted only `graph is not None`, which is VACUOUS:
    # StateGraph.compile() never invokes node bodies and MagicMock() swallows
    # any attribute access silently, so that assertion still passes even if
    # graph.py's per-role wiring is broken entirely (e.g. every node gets the
    # raw llm/dict, or two roles' llms are swapped). Spy on each make_*_node
    # factory — same idiom as test_mixed_node_built_with_erp_query_read_tools
    # above — to capture the actual llm object each node factory is called
    # with, then assert identity against that role's distinct sentinel.
    import backend.src.agents.graph as graph_mod
    from backend.src.agents.models import ROLES
    from backend.src.agents.write_registry import WRITE_COORDINATORS, Spec

    llms = {r: MagicMock(name=r) for r in ROLES}
    captured = {}

    def spy_llm_only(name, real):
        def _spy(llm):
            captured[name] = llm
            return real(llm)
        return _spy

    def spy_llm_tools(name, real):
        def _spy(llm, tools):
            captured[name] = llm
            return real(llm, tools)
        return _spy

    monkeypatch.setattr(graph_mod, "make_intent_router_node",
                         spy_llm_only("intent_router", graph_mod.make_intent_router_node))
    monkeypatch.setattr(graph_mod, "make_erp_read_node",
                         spy_llm_tools("erp_read", graph_mod.make_erp_read_node))
    monkeypatch.setattr(graph_mod, "make_erp_write_planner_node",
                         spy_llm_only("erp_write_planner", graph_mod.make_erp_write_planner_node))
    monkeypatch.setattr(graph_mod, "make_rag_node",
                         spy_llm_only("rag", graph_mod.make_rag_node))
    monkeypatch.setattr(graph_mod, "make_fusion_node",
                         spy_llm_tools("mixed", graph_mod.make_fusion_node))
    monkeypatch.setattr(graph_mod, "make_respond_unknown_node",
                         spy_llm_only("respond_unknown", graph_mod.make_respond_unknown_node))
    monkeypatch.setattr(graph_mod, "make_skill_extract_node",
                         spy_llm_only("skill_extract", graph_mod.make_skill_extract_node))

    # Coordinators (create_order/create_rfq/.../inventory_adjust) receive
    # llms["planner"] too, via spec.build(llms["planner"], tools) in
    # graph.py — spy on each Spec's .build so a role-swap there is caught.
    spied_coordinators = {}
    for tool, spec in WRITE_COORDINATORS.items():
        real_build = spec.build

        def make_spy(node_name, real_build=real_build):
            def _spy(llm, tools):
                captured[node_name] = llm
                return real_build(llm, tools)
            return _spy

        spied_coordinators[tool] = Spec(spec.node, make_spy(spec.node))
    monkeypatch.setattr(graph_mod, "WRITE_COORDINATORS", spied_coordinators)

    graph = build_graph(llms, tools=[], checkpointer=None)
    assert graph is not None

    # Each node got its own role's llm...
    assert captured["intent_router"] is llms["router"]
    assert captured["erp_read"] is llms["read"]
    assert captured["erp_write_planner"] is llms["planner"]
    assert captured["rag"] is llms["synthesis"]
    assert captured["mixed"] is llms["fusion"]
    assert captured["respond_unknown"] is llms["chitchat"]
    assert captured["create_order"] is llms["planner"]
    assert captured["create_rfq"] is llms["planner"]
    assert captured["inventory_adjust"] is llms["planner"]
    assert captured["skill_extract"] is llms["planner"]

    # ...and critically NOT some other role's llm — this is what catches a
    # role-swap bug (e.g. llms["read"] accidentally wired to router/planner).
    assert captured["intent_router"] is not llms["read"]
    assert captured["intent_router"] is not llms["planner"]
    assert captured["erp_read"] is not llms["router"]
    assert captured["erp_read"] is not llms["planner"]
    assert captured["erp_write_planner"] is not llms["read"]
    assert captured["erp_write_planner"] is not llms["router"]
    assert captured["rag"] is not llms["fusion"]
    assert captured["mixed"] is not llms["synthesis"]
    assert captured["respond_unknown"] is not llms["router"]
    assert captured["skill_extract"] is not llms["router"]


def test_build_graph_registers_skill_nodes():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    nodes = graph.get_graph().nodes
    assert {"skill_extract", "skill_discount_quote"} <= set(nodes)
    assert "skill_warehouse_receiving" not in nodes  # removed from SKILLS this branch


def test_skill_nodes_edge_straight_to_end():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("skill_discount_quote", "__end__") in edges


def test_route_by_intent_ignores_skills_when_flag_off(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: False)
    state = {"messages": [HumanMessage(content="báo giá chiết khấu cho Azur")],
            "intent": "erp_write"}
    # Flag off → falls through to state["intent"], byte-identical to today,
    # regardless of the trigger phrase being present in the message.
    assert _route_by_intent(state) == "erp_write"


def test_route_by_intent_routes_to_skill_extract_when_flag_on_and_triggered(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="báo giá chiết khấu cho Azur")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "skill_extract"


def test_route_by_intent_flag_on_but_no_trigger_falls_through(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="xác nhận đơn S00012")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "erp_write"


def test_build_graph_registers_agentic_warehouse_receiving_node():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    assert "skill_agentic_warehouse_receiving" in graph.get_graph().nodes


def test_agentic_node_edges_to_context_sync():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("skill_agentic_warehouse_receiving", "agentic_context_sync") in edges
    assert ("skill_agentic_warehouse_receiving", "__end__") not in edges


def test_route_by_intent_routes_agentic_trigger_to_agentic_node(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="quy trình nhập kho cho P00003")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "skill_agentic_warehouse_receiving"


def test_route_by_intent_agentic_trigger_diacritic_insensitive(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="quy trinh nhap kho cho P00003")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "skill_agentic_warehouse_receiving"


def test_route_by_intent_agentic_trigger_ignored_when_flag_off(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: False)
    state = {"messages": [HumanMessage(content="quy trình nhập kho cho P00003")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "erp_write"


def test_route_by_intent_discount_quote_still_goes_through_skill_extract(monkeypatch):
    # Regression guard: the new agentic-specific check must not shadow the
    # unrelated discount_quote trigger, which still goes through the
    # generic SKILLS/skill_extract path (Task 1 only removed
    # warehouse_receiving from SKILLS, not discount_quote).
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="báo giá chiết khấu cho Azur")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "skill_extract"


def test_build_graph_agentic_nodes_use_planner_role(monkeypatch):
    # Registry-based spy (thay cho 2 spy test per-module cũ — graph.py không
    # còn import từng skill module sau khi registry hóa): mọi agentic skill
    # phải được build với đúng llms["planner"], không phải role khác.
    import dataclasses
    import backend.src.agents.graph as graph_mod
    from backend.src.agents import agentic_registry
    from backend.src.agents.models import ROLES
    llms = {r: MagicMock(name=r) for r in ROLES}
    captured = {}
    for name, spec in list(agentic_registry.AGENTIC_SKILLS.items()):
        def _spy(llm, mcp_tools, _name=name, _real=spec.build):
            captured[_name] = llm
            return _real(llm, mcp_tools)
        monkeypatch.setitem(agentic_registry.AGENTIC_SKILLS, name,
                            dataclasses.replace(spec, build=_spy))
    graph_mod.build_graph(llms, tools=[], checkpointer=None)
    assert set(captured) == set(agentic_registry.AGENTIC_SKILLS)
    for name, llm in captured.items():
        assert llm is llms["planner"], name
        assert llm is not llms["router"], name


def test_build_graph_registers_agentic_delivery_node():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    assert "skill_agentic_delivery" in graph.get_graph().nodes


def test_agentic_delivery_node_edges_to_context_sync():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("skill_agentic_delivery", "agentic_context_sync") in edges
    assert ("skill_agentic_delivery", "__end__") not in edges


def test_route_by_intent_routes_delivery_trigger_to_delivery_node(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="giao hàng cho đơn bán S00012")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "skill_agentic_delivery"


def test_route_by_intent_delivery_trigger_diacritic_insensitive(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="giao hang cho don ban S00012")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "skill_agentic_delivery"


def test_route_by_intent_delivery_trigger_ignored_when_flag_off(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: False)
    state = {"messages": [HumanMessage(content="giao hàng cho đơn bán S00012")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "erp_write"


def test_route_by_intent_warehouse_receiving_trigger_unaffected_by_delivery_addition(monkeypatch):
    # Regression guard: thêm nhánh check delivery TRƯỚC nhánh warehouse_
    # receiving trong _route_by_intent không được làm hỏng routing của
    # skill anh em đã merge.
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="quy trình nhập kho cho P00003")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "skill_agentic_warehouse_receiving"


def test_route_by_intent_discount_quote_unaffected_by_delivery_addition(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="báo giá chiết khấu cho Azur")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "skill_extract"


def test_build_graph_registers_every_agentic_registry_entry():
    from backend.src.agents.agentic_registry import AGENTIC_SKILLS
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    nodes = graph.get_graph().nodes
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    for spec in AGENTIC_SKILLS.values():
        assert spec.node in nodes
        assert (spec.node, "agentic_context_sync") in edges


def test_route_by_intent_trigger_with_rag_intent_not_hijacked(monkeypatch):
    # Intent-gate: câu HỎI về quy trình ("... là gì?") chứa cụm trigger
    # nhưng router phân loại rag → phải đi RAG, không bị cướp vào THỰC THI
    # SOP. Đây là lỗ hổng từng buộc ERP_SKILLS_ENABLED default-off.
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="quy trình nhập kho là gì?")],
            "intent": "rag"}
    assert _route_by_intent(state) == "rag"


def test_route_by_intent_trigger_with_read_intent_not_hijacked(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(
                content="kiểm tra tình trạng giao hàng theo đơn S00074")],
            "intent": "erp_read"}
    assert _route_by_intent(state) == "erp_read"


def test_route_by_intent_discount_trigger_with_rag_intent_not_hijacked(monkeypatch):
    # Gate áp cho cả nhánh match_skill (#2) — hỏi chính sách chiết khấu
    # không bị đưa vào skill_extract.
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(
                content="chính sách báo giá chiết khấu như thế nào?")],
            "intent": "rag"}
    assert _route_by_intent(state) == "rag"


# ── Regression: live-verify 2026-07-16 found the AND-only gate (intent ==
# "erp_write") caused the MIRROR-IMAGE bug — real commands using the skill's
# own literal TRIGGERS phrases got misrouted away from the skill because the
# router classified them "mixed"/"erp_read" (ambiguous under prompts.py's
# own "mixed" definition, which the router was never tuned to disambiguate
# from an execute-SOP-for-order-X command). Fixed by switching to an OR:
# erp_write OR "does not look like a question" (deterministic marker check,
# _looks_like_question). These tests reproduce the exact 3 failing repro
# phrasings with the WRONG intent attached, and assert routing now succeeds
# regardless of router classification. ──────────────────────────────────

def test_looks_like_question_detects_all_markers():
    from backend.src.agents.graph import _looks_like_question
    from backend.src.agents.skills import _fold
    questions = [
        "quy trình nhập kho là gì?",
        "kiểm tra tình trạng giao hàng theo đơn S00074",
        "chính sách báo giá chiết khấu như thế nào?",
        "quy trình nhập kho nghĩa là gì",
        "tại sao phải làm quy trình nhập kho",
        "giải thích quy trình nhập kho giúp tôi",
        "hướng dẫn quy trình nhập kho",
        "trạng thái đơn mua P00021 thế nào",
        "đơn này có xác nhận được không",
    ]
    for q in questions:
        assert _looks_like_question(_fold(q)), q


def test_looks_like_question_false_for_plain_commands():
    from backend.src.agents.graph import _looks_like_question
    from backend.src.agents.skills import _fold
    commands = [
        "làm quy trình nhập kho cho đơn mua P00021",
        "nhập kho theo quy trình cho đơn mua P00021",
        "quy trình nhập kho cho đơn mua P00021",
        "giao hàng cho đơn bán S00012",
        "báo giá chiết khấu cho Cửa hàng ABC, 5 Tủ gỗ",
    ]
    for c in commands:
        assert not _looks_like_question(_fold(c)), c


def test_route_by_intent_warehouse_command_routes_despite_mixed_intent(monkeypatch):
    # Repro of live-verify failure #1: router classified this exact command
    # as "mixed" (procedure + specific order, per prompts.py's own
    # definition) instead of "erp_write" — the AND-only gate silently ate
    # the command. Must now route to the skill regardless.
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(
                content="làm quy trình nhập kho cho đơn mua P00021")],
            "intent": "mixed"}
    assert _route_by_intent(state) == "skill_agentic_warehouse_receiving"


def test_route_by_intent_warehouse_command_routes_despite_erp_read_intent(monkeypatch):
    # Repro of live-verify failure #2 (the worst one): router classified
    # this command — using the skill's own literal TRIGGERS phrase verbatim
    # — as "erp_read", producing an unrelated purchase-order summary
    # instead of ever reaching the skill.
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(
                content="nhập kho theo quy trình cho đơn mua P00021")],
            "intent": "erp_read"}
    assert _route_by_intent(state) == "skill_agentic_warehouse_receiving"


def test_route_by_intent_delivery_command_routes_despite_wrong_intent(monkeypatch):
    # Same fix, mirrored for the delivery skill.
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(content="giao hàng cho đơn bán S00012")],
            "intent": "mixed"}
    assert _route_by_intent(state) == "skill_agentic_delivery"


def test_route_by_intent_discount_command_routes_despite_wrong_intent(monkeypatch):
    from backend.src.agents.graph import _route_by_intent
    from backend.src.agents import skill_gate
    monkeypatch.setattr(skill_gate, "skills_enabled", lambda: True)
    state = {"messages": [HumanMessage(
                content="báo giá chiết khấu cho Cửa hàng ABC, 5 Tủ gỗ")],
            "intent": "erp_read"}
    assert _route_by_intent(state) == "skill_extract"


def test_agentic_context_sync_registered_and_edges_to_end():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    assert "agentic_context_sync" in graph.get_graph().nodes
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("agentic_context_sync", "__end__") in edges


def test_route_by_intent_env_kill_switch_zero_disables_skills(monkeypatch):
    # Minor còn treo từ final review Đợt 1: 1 assertion tích hợp xuyên qua
    # skill_gate THẬT (env var, không monkeypatch hàm) — ERP_SKILLS_ENABLED=0
    # + trigger + intent erp_write → về planner tầng 1, không skill.
    from backend.src.agents.graph import _route_by_intent
    monkeypatch.setenv("ERP_SKILLS_ENABLED", "0")
    state = {"messages": [HumanMessage(content="quy trình nhập kho cho P00003")],
            "intent": "erp_write"}
    assert _route_by_intent(state) == "erp_write"


@pytest.mark.asyncio
async def test_agentic_skill_recursion_bounded_in_real_graph(monkeypatch):
    # Mirror spike v10b QUA build_graph thật: trước fix này, một model loop
    # vô hạn trong subgraph chạy KHÔNG GIỚI HẠN (61+ lượt, tripwire spike);
    # với with_config(AGENTIC_RECURSION_LIMIT=15) tại wiring, nó phải raise
    # GraphRecursionError và model bị chặn ở ~7-8 lượt gọi.
    import dataclasses
    import json as _json
    from pydantic import PrivateAttr
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatResult, ChatGeneration
    from langchain_core.tools import tool
    from langchain_core.messages import AIMessage
    from langgraph.errors import GraphRecursionError
    import backend.src.agents.graph as graph_mod
    from backend.src.agents import agentic_registry

    calls = {"n": 0}

    @tool("lookup")
    def lookup(ref: str) -> str:
        """Fake read tool."""
        return _json.dumps({"status": "success", "ref": ref})

    class _RouterAndLoopModel(BaseChatModel):
        # Vai router: trả "erp_write" cho lượt phân loại intent (message
        # system là INTENT_ROUTER_PROMPT). Vai skill-agent: loop tool-call
        # vô hạn. Phân biệt bằng việc lượt router không có tool nào bind —
        # đơn giản hơn: câu system của router chứa "erp_read" (danh sách
        # intent) — check chuỗi đó.
        _dummy: list = PrivateAttr(default_factory=list)

        @property
        def _llm_type(self) -> str:
            return "routerloop"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            sys_text = messages[0].content if messages else ""
            if "erp_read" in sys_text and "erp_write" in sys_text:
                return ChatResult(generations=[ChatGeneration(
                    message=AIMessage(content="erp_write"))])
            calls["n"] += 1
            if calls["n"] > 60:
                raise AssertionError("unbounded: model reached 61 calls")
            msg = AIMessage(content="", tool_calls=[
                {"name": "lookup", "args": {"ref": "P1"}, "id": f"l{calls['n']}"}])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    model = _RouterAndLoopModel()

    def _looping_build(llm, mcp_tools):
        return create_agent(model, [lookup], system_prompt="t")

    spec = agentic_registry.AGENTIC_SKILLS["warehouse_receiving"]
    monkeypatch.setitem(agentic_registry.AGENTIC_SKILLS, "warehouse_receiving",
                        dataclasses.replace(spec, build=_looping_build))
    monkeypatch.delenv("ERP_SKILLS_ENABLED", raising=False)  # default ON

    graph = graph_mod.build_graph(model, tools=[], checkpointer=None)
    with pytest.raises(GraphRecursionError):
        await graph.ainvoke({"messages": [
            HumanMessage(content="quy trình nhập kho cho P00003")]})
    assert calls["n"] <= 8, f"limit 15 phải chặn ở ~7-8 lượt, thấy {calls['n']}"
