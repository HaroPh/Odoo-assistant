# backend/tests/agents/test_chat_resume.py
"""Phase 3: chat() interrupt surfacing + resume wiring.

These drive ERPAgent.chat() with a fake compiled graph so the HITL loop can be
tested without Postgres / MCP / a real LLM.
"""
import os
import sys
import pytest
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import AIMessage, RemoveMessage
from langgraph.types import Command
from langgraph.graph.message import REMOVE_ALL_MESSAGES

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.erp_agent import ERPAgent, _pending_expiry


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
    agent._llms = {"evaluator": llm or MagicMock()}
    return agent


QUESTION = "Bạn có chắc muốn thực hiện: **Tạo đơn hàng cho khách 42**? (có / không)"


# ── Feature 1: _pending_expiry reader ─────────────────────────────────────────

def test_pending_expiry_reads_timestamp():
    snap = _FakeSnapshot(
        next_=("erp_write_planner",),
        tasks=(_FakeTask((_FakeInterrupt({"question": QUESTION, "expires_at": 1234.5}),)),))
    assert _pending_expiry(snap) == 1234.5


def test_pending_expiry_returns_none_when_absent():
    snap = _FakeSnapshot(
        next_=("erp_write_planner",),
        tasks=(_FakeTask((_FakeInterrupt({"question": QUESTION}),)),))
    assert _pending_expiry(snap) is None


# ── Feature 1: parked + not expired → normal resume ───────────────────────────

async def test_parked_not_expired_resumes_normally(monkeypatch):
    import backend.src.agents.erp_agent as agent_mod
    monkeypatch.setattr(agent_mod.time, "time", lambda: 10_000.0)

    snapshot = _FakeSnapshot(
        next_=("erp_write_planner",),
        tasks=(_FakeTask((_FakeInterrupt({"question": QUESTION, "expires_at": 99_999.0}),)),))
    result = {"messages": [AIMessage(content="[STUB] Đã thực hiện thành công.")]}
    graph = _FakeGraph(snapshot, result)
    agent = _agent_with(graph)

    answer = await agent.chat([{"role": "user", "content": "có"}], thread_id="T1")

    assert answer == "[STUB] Đã thực hiện thành công."
    graph.ainvoke.assert_awaited_once()
    sent = graph.ainvoke.await_args.args[0]
    assert isinstance(sent, Command) and sent.resume is True


# ── Feature 1: parked + expired → discard stale, process new turn fresh ────────

async def test_parked_expired_discards_and_processes_fresh(monkeypatch):
    import backend.src.agents.erp_agent as agent_mod
    monkeypatch.setattr(agent_mod.time, "time", lambda: 10_000.0)

    snapshot = _FakeSnapshot(
        next_=("erp_write_planner",),
        tasks=(_FakeTask((_FakeInterrupt({"question": QUESTION, "expires_at": 9_000.0}),)),))
    graph = _FakeGraph(snapshot, None)
    drain_result = {"messages": [AIMessage(content="Đã hủy thao tác.")]}
    fresh_result = {"messages": [AIMessage(content="Tồn kho hiện tại là 500.")]}
    graph.ainvoke = AsyncMock(side_effect=[drain_result, fresh_result])

    llm = MagicMock()
    llm.ainvoke = AsyncMock()  # classifier must NOT run on expiry
    agent = _agent_with(graph, llm=llm)

    answer = await agent.chat([{"role": "user", "content": "tồn kho large cabinet?"}],
                              thread_id="T1")

    assert answer == "Tồn kho hiện tại là 500."
    assert graph.ainvoke.await_count == 2
    first_arg = graph.ainvoke.await_args_list[0].args[0]
    assert isinstance(first_arg, Command) and first_arg.resume is False
    second_arg = graph.ainvoke.await_args_list[1].args[0]
    assert isinstance(second_arg, dict)
    assert isinstance(second_arg["messages"][0], RemoveMessage)
    assert second_arg["messages"][0].id == REMOVE_ALL_MESSAGES
    llm.ainvoke.assert_not_awaited()


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


# ── Feature 2: non-resume turn overwrites persisted history (no accumulation) ──

async def test_not_parked_overwrites_message_history():
    snapshot = _FakeSnapshot(next_=())  # not parked
    result = {"messages": [AIMessage(content="ans")]}
    graph = _FakeGraph(snapshot, result)
    agent = _agent_with(graph)

    incoming = [{"role": "user", "content": "A"},
                {"role": "assistant", "content": "ans-old"},
                {"role": "user", "content": "B"}]
    await agent.chat(incoming, thread_id="T1")

    sent = graph.ainvoke.await_args.args[0]
    assert isinstance(sent, dict)
    msgs = sent["messages"]
    assert isinstance(msgs[0], RemoveMessage)
    assert msgs[0].id == REMOVE_ALL_MESSAGES
    assert msgs[1:] == incoming
