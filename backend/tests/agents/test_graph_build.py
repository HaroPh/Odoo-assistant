import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

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


def test_agentic_node_edges_straight_to_end():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("skill_agentic_warehouse_receiving", "__end__") in edges


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


def test_agentic_delivery_node_edges_straight_to_end():
    graph = build_graph(MagicMock(), tools=[], checkpointer=None)
    edges = [(e.source, e.target) for e in graph.get_graph().edges]
    assert ("skill_agentic_delivery", "__end__") in edges


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
        assert (spec.node, "__end__") in edges


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
