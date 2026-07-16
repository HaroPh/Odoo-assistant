# backend/tests/agents/test_erp_agent_resume.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from langgraph.types import Command
from backend.src.agents.erp_agent import _decide_resume


@pytest.mark.asyncio
async def test_free_text_passes_raw_reply_through():
    result = await _decide_resume("free_text", [], "Số lượng?", "45 cái", llm=None)
    assert isinstance(result, Command)
    assert result.resume == "45 cái"


@pytest.mark.asyncio
async def test_free_text_passes_through_even_when_reply_looks_like_yes_no():
    # The whole point of free_text is NOT running it through the yes/no
    # classifier — "có" here is a quantity-ish reply in a hypothetical
    # skill, not a confirmation, and must not be coerced to a bool.
    result = await _decide_resume("free_text", [], "Q?", "có 12 thùng", llm=None)
    assert result.resume == "có 12 thùng"


@pytest.mark.asyncio
async def test_disambiguation_kind_unchanged(monkeypatch):
    options = [{"id": "a", "name": "Azur"}, {"id": "b", "name": "Bazaar"}]
    result = await _decide_resume("disambiguation", options, "Chọn?", "1", llm=None)
    assert isinstance(result, Command) and result.resume == "a"


@pytest.mark.asyncio
async def test_disambiguation_kind_unresolved_reply_reasks():
    options = [{"id": "a", "name": "Azur"}, {"id": "b", "name": "Bazaar"}]
    result = await _decide_resume("disambiguation", options, "Chọn?", "xyz", llm=None)
    assert result == "Chọn?"


@pytest.mark.asyncio
async def test_confirm_default_kind_unchanged_keyword_fastpath():
    # "có" hits confirmation.py's keyword fast-path — no llm call needed.
    result = await _decide_resume("confirm", [], "Xác nhận?", "có", llm=None)
    assert isinstance(result, Command) and result.resume is True


@pytest.mark.asyncio
async def test_confirm_default_kind_cancel_keyword_fastpath():
    result = await _decide_resume("confirm", [], "Xác nhận?", "không", llm=None)
    assert isinstance(result, Command) and result.resume is False


@pytest.mark.asyncio
async def test_next_action_kind_unchanged():
    options = [{"id": True, "name": "Giao hàng"}, {"id": False, "name": "Dừng"}]
    result = await _decide_resume("next_action", options, "Tiếp?", "1", llm=None)
    assert isinstance(result, Command) and result.resume is True


@pytest.mark.asyncio
async def test_chat_returns_polite_message_on_graph_recursion_error():
    # Trước fix: GraphRecursionError xuyên lên catch-all của main.py → câu
    # lỗi generic. Sau fix: erp_agent.chat bắt riêng → RECURSION_MSG trung
    # thực (không khẳng định "chưa ghi gì" — write có thể đã xảy ra qua
    # cổng xác nhận trước khi loop).
    from types import SimpleNamespace
    from langgraph.errors import GraphRecursionError
    from backend.src.agents.erp_agent import ERPAgent, RECURSION_MSG

    class _RecursionGraph:
        async def aget_state(self, config):
            return SimpleNamespace(tasks=(), next=())

        async def ainvoke(self, *a, **k):
            raise GraphRecursionError("loop")

    agent = ERPAgent()
    agent.graph = _RecursionGraph()
    out = await agent.chat([{"role": "user", "content": "nhập kho P00021"}])
    assert out == RECURSION_MSG
