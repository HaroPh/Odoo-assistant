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
async def test_rag_node_synthesizes_answer(monkeypatch):
    import backend.src.agents.nodes as nodes_mod
    from backend.src.rag.types import Chunk, RetrievalResult
    c = Chunk(chunk_id=1, doc_id="d", source_file="C:/docs/policy.docx", doc_title="P",
              section_path="Chính sách hoàn hàng › Mục 1", page=1, sheet=None,
              row_range=None, text="Hoàn hàng trong 30 ngày.", dense_score=0.7,
              sparse_score=None, rrf_score=0.02, rank=0)
    result = RetrievalResult(query="q", query_used="q", chunks=[c], top_score=0.02,
                             total_candidates=1, method="hybrid-rrf")
    captured = {}

    def fake_retrieve(query, *a, **kw):
        captured["query"] = query
        return result

    monkeypatch.setattr(nodes_mod, "retrieve", fake_retrieve)

    from backend.src.agents.nodes import make_rag_node
    node = make_rag_node(make_mock_llm("Khách được hoàn trong 30 ngày."))
    out = await node(_state("khách hoàn hàng mấy ngày?"))
    assert captured["query"] == "khách hoàn hàng mấy ngày?"
    content = out["messages"][0].content
    assert "Khách được hoàn trong 30 ngày." in content
    assert "📄 Nguồn:" in content


@pytest.mark.asyncio
async def test_rag_node_safe_message_on_retrieve_error(monkeypatch):
    import backend.src.agents.nodes as nodes_mod
    from backend.src.agents.synthesis import SAFE_MSG

    def boom(query, *a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(nodes_mod, "retrieve", boom)

    from backend.src.agents.nodes import make_rag_node
    node = make_rag_node(make_mock_llm("unused"))
    out = await node(_state("bất kỳ"))
    assert out["messages"][0].content == SAFE_MSG


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
