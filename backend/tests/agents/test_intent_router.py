# backend/tests/agents/test_intent_router.py
import pytest
from langchain_core.messages import HumanMessage
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.state import ERPAgentState
from backend.tests.conftest import make_mock_llm


def _state(text: str) -> ERPAgentState:
    return ERPAgentState(
        messages=[HumanMessage(content=text)],
        intent=None,
        pending_action=None,
        confirmed=None,
    )


@pytest.mark.asyncio
async def test_router_erp_read():
    from backend.src.agents.nodes import make_intent_router_node
    node = make_intent_router_node(make_mock_llm("erp_read"))
    result = await node(_state("Đơn hàng nào đang trễ?"))
    assert result["intent"] == "erp_read"


@pytest.mark.asyncio
async def test_router_erp_write():
    from backend.src.agents.nodes import make_intent_router_node
    node = make_intent_router_node(make_mock_llm("erp_write"))
    result = await node(_state("Tạo đơn hàng cho khách Nguyễn Văn A"))
    assert result["intent"] == "erp_write"


@pytest.mark.asyncio
async def test_router_rag():
    from backend.src.agents.nodes import make_intent_router_node
    node = make_intent_router_node(make_mock_llm("rag"))
    result = await node(_state("Quy trình nhập kho là gì?"))
    assert result["intent"] == "rag"


@pytest.mark.asyncio
async def test_router_unknown():
    from backend.src.agents.nodes import make_intent_router_node
    node = make_intent_router_node(make_mock_llm("unknown"))
    result = await node(_state("Xin chào"))
    assert result["intent"] == "unknown"


@pytest.mark.asyncio
async def test_router_invalid_llm_response_falls_back_to_unknown():
    """If LLM returns garbage, router must return 'unknown' not crash."""
    from backend.src.agents.nodes import make_intent_router_node
    node = make_intent_router_node(make_mock_llm("I don't know, maybe erp?"))
    result = await node(_state("blah"))
    assert result["intent"] == "unknown"


@pytest.mark.asyncio
async def test_router_empty_messages():
    """Empty message list → unknown, no crash."""
    from backend.src.agents.nodes import make_intent_router_node
    node = make_intent_router_node(make_mock_llm("erp_read"))
    state = ERPAgentState(messages=[], intent=None, pending_action=None, confirmed=None)
    result = await node(state)
    assert result["intent"] == "unknown"
