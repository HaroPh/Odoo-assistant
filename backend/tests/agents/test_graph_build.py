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
