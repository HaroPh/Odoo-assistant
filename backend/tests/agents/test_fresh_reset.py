# backend/tests/agents/test_fresh_reset.py
"""R7 Lớp B: fresh-conversation reset — turn ĐẦU của hội thoại mới (thread id
do server tự suy ra) wipe state cũ parked dưới cùng thread id (adelete_thread)
và không bao giờ vào nhánh resume. Fakes theo pattern test_chat_resume.py
(fake compiled graph — không cần Postgres / MCP / LLM)."""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES
from langgraph.types import Command

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.erp_agent import ERPAgent

QUESTION = "Bạn có chắc muốn thực hiện: **Tạo đơn hàng cho khách 42**? (có / không)"


class _FakeInterrupt:
    def __init__(self, value):
        self.value = value


class _FakeTask:
    def __init__(self, interrupts):
        self.interrupts = interrupts


class _FakeSnapshot:
    def __init__(self, next_=(), tasks=()):
        self.next = next_
        self.tasks = tasks


def _parked_snapshot():
    return _FakeSnapshot(
        next_=("erp_write_planner",),
        tasks=(_FakeTask((_FakeInterrupt({"question": QUESTION}),)),))


class _FakeGraph:
    def __init__(self, snapshot, invoke_result):
        self.ainvoke = AsyncMock(return_value=invoke_result)
        self.aget_state = AsyncMock(return_value=snapshot)


def _agent_with(graph):
    agent = ERPAgent()
    agent.graph = graph
    agent._llms = {"evaluator": MagicMock()}
    agent._checkpointer = MagicMock()
    agent._checkpointer.adelete_thread = AsyncMock()
    return agent


async def test_fresh_reset_wipes_thread_and_skips_resume():
    # Thread T1 đang parked bởi hội thoại CŨ; hội thoại MỚI (1 user message,
    # reset_if_fresh=True) phải wipe và chạy fresh — không được hiểu message
    # là câu trả lời confirm của hội thoại cũ.
    graph = _FakeGraph(_parked_snapshot(),
                       {"messages": [AIMessage(content="Tồn kho là 500.")]})
    agent = _agent_with(graph)

    answer = await agent.chat(
        [{"role": "user", "content": "tồn kho large cabinet?"}],
        thread_id="T1", reset_if_fresh=True)

    assert answer == "Tồn kho là 500."
    agent._checkpointer.adelete_thread.assert_awaited_once_with("T1")
    graph.aget_state.assert_not_awaited()      # bỏ qua hẳn nhánh resume
    sent = graph.ainvoke.await_args.args[0]    # chạy như fresh turn
    assert isinstance(sent, dict)
    assert isinstance(sent["messages"][0], RemoveMessage)
    assert sent["messages"][0].id == REMOVE_ALL_MESSAGES


async def test_multi_turn_history_never_resets():
    graph = _FakeGraph(_FakeSnapshot(), {"messages": [AIMessage(content="ok")]})
    agent = _agent_with(graph)

    await agent.chat([{"role": "user", "content": "A"},
                      {"role": "assistant", "content": "b"},
                      {"role": "user", "content": "C"}],
                     thread_id="T1", reset_if_fresh=True)

    agent._checkpointer.adelete_thread.assert_not_awaited()
    graph.aget_state.assert_awaited_once()


async def test_two_user_messages_no_assistant_never_resets():
    # [user1, user2] (turn 1 lỗi, user gõ tiếp): KHÔNG wipe — predicate là
    # "đúng 1 message", không phải "0 assistant message".
    graph = _FakeGraph(_FakeSnapshot(), {"messages": [AIMessage(content="ok")]})
    agent = _agent_with(graph)

    await agent.chat([{"role": "user", "content": "A"},
                      {"role": "user", "content": "B"}],
                     thread_id="T1", reset_if_fresh=True)

    agent._checkpointer.adelete_thread.assert_not_awaited()


async def test_no_thread_id_never_resets():
    graph = _FakeGraph(_FakeSnapshot(), {"messages": [AIMessage(content="ok")]})
    agent = _agent_with(graph)

    await agent.chat([{"role": "user", "content": "A"}], reset_if_fresh=True)

    agent._checkpointer.adelete_thread.assert_not_awaited()


async def test_default_false_preserves_resume_semantics():
    # Client session_id gửi câu confirm dạng 1 message; default
    # (reset_if_fresh=False) phải resume parked confirm — Command(resume=True)
    # — chứ không wipe. ("có" đi qua keyword fast-path, không chạm llm fake.)
    graph = _FakeGraph(_parked_snapshot(),
                       {"messages": [AIMessage(content="[STUB] Đã thực hiện thành công.")]})
    agent = _agent_with(graph)

    answer = await agent.chat([{"role": "user", "content": "có"}], thread_id="T1")

    assert answer == "[STUB] Đã thực hiện thành công."
    agent._checkpointer.adelete_thread.assert_not_awaited()
    sent = graph.ainvoke.await_args.args[0]
    assert isinstance(sent, Command) and sent.resume is True


# ── R7 hotfix (live-verify 2026-07-09): stateless answer path for Open WebUI's
# own background task calls (title/tags/follow-up-gen) — must NEVER touch
# thread/checkpoint state (no graph, no checkpointer at all).

async def test_answer_stateless_uses_chitchat_llm_never_touches_graph():
    graph = _FakeGraph(_parked_snapshot(), {"messages": [AIMessage(content="unused")]})
    agent = _agent_with(graph)
    agent._llms["chitchat"] = MagicMock()
    agent._llms["chitchat"].ainvoke = AsyncMock(
        return_value=AIMessage(content='{"title": "Cabinet Pricing"}'))

    answer = await agent.answer_stateless("### Task:\nGenerate a title...")

    assert answer == '{"title": "Cabinet Pricing"}'
    agent._llms["chitchat"].ainvoke.assert_awaited_once()
    sent = agent._llms["chitchat"].ainvoke.await_args.args[0]
    assert len(sent) == 1 and sent[0].content == "### Task:\nGenerate a title..."
    graph.ainvoke.assert_not_awaited()
    graph.aget_state.assert_not_awaited()
    agent._checkpointer.adelete_thread.assert_not_awaited()
