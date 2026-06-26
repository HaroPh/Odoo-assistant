# backend/tests/agents/test_chat_resume.py
"""Phase 3: chat() interrupt surfacing + resume wiring.

These drive ERPAgent.chat() with a fake compiled graph so the HITL loop can be
tested without Postgres / MCP / a real LLM.
"""
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import AIMessage
from langgraph.types import Command

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.erp_agent import ERPAgent


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


class _FakeGraph:
    def __init__(self, snapshot, invoke_result):
        self._snapshot = snapshot
        self.ainvoke = AsyncMock(return_value=invoke_result)

    async def aget_state(self, config):
        return self._snapshot


def _agent_with(graph, llm=None):
    agent = ERPAgent()
    agent.graph = graph
    agent._llm = llm or MagicMock()
    return agent


QUESTION = "Bạn có chắc muốn thực hiện: **Tạo đơn hàng cho khách 42**? (có / không)"


# ── output side: a fresh interrupt is surfaced as the assistant reply ──────────

async def test_new_interrupt_returns_question():
    snapshot = _FakeSnapshot(next_=())  # not parked
    result = {"__interrupt__": (_FakeInterrupt({"question": QUESTION, "action": {}}),)}
    agent = _agent_with(_FakeGraph(snapshot, result))

    answer = await agent.chat([{"role": "user", "content": "Tạo đơn cho khách 42"}],
                              thread_id="T1")
    assert answer == QUESTION


# ── input side: parked thread + clear yes → resume(True) ──────────────────────

async def test_parked_confirm_resumes_true():
    snapshot = _FakeSnapshot(next_=("erp_write_planner",),
                             tasks=(_FakeTask((_FakeInterrupt({"question": QUESTION}),)),))
    result = {"messages": [AIMessage(content="[STUB] Đã thực hiện thành công.")]}
    graph = _FakeGraph(snapshot, result)
    agent = _agent_with(graph)

    answer = await agent.chat([{"role": "user", "content": "có"}], thread_id="T1")

    assert answer == "[STUB] Đã thực hiện thành công."
    graph.ainvoke.assert_awaited_once()
    sent = graph.ainvoke.await_args.args[0]
    assert isinstance(sent, Command)
    assert sent.resume is True


# ── input side: parked thread + clear no → resume(False) ──────────────────────

async def test_parked_cancel_resumes_false():
    snapshot = _FakeSnapshot(next_=("erp_write_planner",),
                             tasks=(_FakeTask((_FakeInterrupt({"question": QUESTION}),)),))
    result = {"messages": [AIMessage(content="Đã hủy thao tác.")]}
    graph = _FakeGraph(snapshot, result)
    agent = _agent_with(graph)

    answer = await agent.chat([{"role": "user", "content": "không"}], thread_id="T1")

    assert answer == "Đã hủy thao tác."
    sent = graph.ainvoke.await_args.args[0]
    assert isinstance(sent, Command)
    assert sent.resume is False


# ── input side: parked thread + ambiguous → re-ask, do NOT resume ─────────────

async def test_parked_unclear_reasks_without_resuming():
    snapshot = _FakeSnapshot(next_=("erp_write_planner",),
                             tasks=(_FakeTask((_FakeInterrupt({"question": QUESTION}),)),))
    graph = _FakeGraph(snapshot, {"messages": [AIMessage(content="should not be used")]})
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=AIMessage(content="UNCLEAR"))
    agent = _agent_with(graph, llm=llm)

    answer = await agent.chat([{"role": "user", "content": "nó sẽ làm gì vậy"}],
                              thread_id="T1")

    assert answer == QUESTION
    graph.ainvoke.assert_not_awaited()  # graph stays parked
