# backend/tests/agents/test_simple_nodes.py
import pytest
from langchain_core.messages import HumanMessage, AIMessage
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.state import ERPAgentState
from backend.tests.conftest import make_mock_llm


def _state(text: str) -> ERPAgentState:
    return ERPAgentState(
        messages=[HumanMessage(content=text)],
        intent=None, pending_action=None, confirmed=None,
    )


@pytest.mark.asyncio
async def test_rag_node_returns_stub():
    from backend.src.agents.nodes import rag_node
    result = await rag_node(_state("Quy trình nhập kho?"))
    msgs = result["messages"]
    assert len(msgs) == 1
    assert "chưa khả dụng" in msgs[0].content.lower() or "phase 2" in msgs[0].content.lower()


@pytest.mark.asyncio
async def test_respond_unknown_node_calls_llm():
    from backend.src.agents.nodes import make_respond_unknown_node
    llm = make_mock_llm("Xin chào! Tôi có thể giúp gì cho bạn?")
    node = make_respond_unknown_node(llm)
    result = await node(_state("xin chào"))
    msgs = result["messages"]
    assert len(msgs) == 1
    assert "xin chào" in msgs[0].content.lower() or "giúp" in msgs[0].content.lower()


@pytest.mark.asyncio
async def test_erp_read_node_invokes_agent(monkeypatch):
    """erp_read node calls the inner agent and returns its last message."""
    from unittest.mock import AsyncMock, MagicMock
    from backend.src.agents.nodes import make_erp_read_node
    from langchain_core.messages import HumanMessage, AIMessage

    # Stub an inner agent whose ainvoke returns a messages dict
    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(return_value={
        "messages": [
            HumanMessage(content="query"),
            AIMessage(content="Kết quả: 5 đơn trễ"),
        ]
    })

    # Patch create_agent used inside make_erp_read_node
    import backend.src.agents.nodes as nodes_mod
    monkeypatch.setattr(nodes_mod, "_create_agent", lambda *a, **kw: mock_agent)

    node = make_erp_read_node(llm=MagicMock(), tools=[])
    state = ERPAgentState(
        messages=[HumanMessage(content="Đơn nào trễ?")],
        intent="erp_read", pending_action=None, confirmed=None,
    )
    result = await node(state)
    # Should return only the new AI message
    assert any("trễ" in m.content for m in result["messages"])
