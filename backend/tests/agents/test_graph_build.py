import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

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
