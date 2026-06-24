# backend/tests/agents/test_confirmation_gate.py
import pytest
import os
import json
from unittest.mock import AsyncMock, MagicMock
from langchain_core.messages import HumanMessage, AIMessage
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.state import ERPAgentState
from backend.tests.conftest import make_mock_llm


def _write_state(text: str = "Tạo đơn") -> ERPAgentState:
    return ERPAgentState(
        messages=[HumanMessage(content=text)],
        intent="erp_write", pending_action=None, confirmed=None,
    )


# ── Locked (default) ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_planner_locked_returns_not_activated(monkeypatch):
    """When WRITE_ACTIONS_ENABLED != 'true', planner returns locked message."""
    monkeypatch.delenv("WRITE_ACTIONS_ENABLED", raising=False)

    from backend.src.agents.nodes import make_erp_write_planner_node
    node = make_erp_write_planner_node(make_mock_llm("{}"))
    result = await node(_write_state())
    msgs = result["messages"]
    assert len(msgs) == 1
    assert "chưa" in msgs[0].content.lower() or "kích hoạt" in msgs[0].content.lower()


# ── Enabled: interrupt path ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_write_planner_enabled_calls_interrupt(monkeypatch):
    """When enabled, planner calls interrupt() with confirmation question."""
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")

    plan_json = json.dumps({
        "tool": "create_sale_order",
        "args": {"customer_id": 42},
        "summary": "Tạo đơn hàng cho khách id 42",
    })
    llm = make_mock_llm(plan_json)

    interrupted_with = {}

    class _FakeInterrupt(Exception):
        pass

    def fake_interrupt(payload):
        interrupted_with["payload"] = payload
        raise _FakeInterrupt("interrupt called")  # simulate interrupt stopping execution

    import backend.src.agents.nodes as nodes_mod
    monkeypatch.setattr(nodes_mod, "_interrupt", fake_interrupt)

    # Import after monkeypatching — no reload needed since we patch the module attribute
    from backend.src.agents.nodes import make_erp_write_planner_node
    node = make_erp_write_planner_node(llm)

    with pytest.raises(_FakeInterrupt):
        await node(_write_state())

    assert "question" in interrupted_with["payload"]
    assert "42" in interrupted_with["payload"]["question"] or "tạo" in interrupted_with["payload"]["question"].lower()


# ── Executor ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_executor_confirmed_true_returns_success():
    from backend.src.agents.nodes import erp_write_executor_node
    state = ERPAgentState(
        messages=[HumanMessage("Tạo đơn")],
        intent="erp_write",
        pending_action={"tool": "create_sale_order", "args": {}, "summary": "test"},
        confirmed=True,
    )
    result = await erp_write_executor_node(state)
    assert "thành công" in result["messages"][0].content.lower() or "thực hiện" in result["messages"][0].content.lower()


@pytest.mark.asyncio
async def test_planner_handles_missing_summary_key(monkeypatch):
    """When planner JSON has no 'summary' key, fallback to 'tool' value — no KeyError."""
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")

    plan_json = json.dumps({
        "tool": "create_sale_order",
        "args": {},
    })  # no 'summary' key
    llm = make_mock_llm(plan_json)

    interrupted_with = {}

    class _FakeInterrupt(Exception):
        pass

    def fake_interrupt(payload):
        interrupted_with["payload"] = payload
        raise _FakeInterrupt("interrupt called")

    import backend.src.agents.nodes as nodes_mod
    monkeypatch.setattr(nodes_mod, "_interrupt", fake_interrupt)

    from backend.src.agents.nodes import make_erp_write_planner_node
    node = make_erp_write_planner_node(llm)

    with pytest.raises(_FakeInterrupt):
        await node(_write_state())

    assert "question" in interrupted_with["payload"]
    assert "create_sale_order" in interrupted_with["payload"]["question"]


# ── Executor ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_executor_confirmed_false_returns_cancel():
    from backend.src.agents.nodes import erp_write_executor_node
    state = ERPAgentState(
        messages=[HumanMessage("Tạo đơn")],
        intent="erp_write",
        pending_action={"tool": "create_sale_order", "args": {}, "summary": "test"},
        confirmed=False,
    )
    result = await erp_write_executor_node(state)
    assert "hủy" in result["messages"][0].content.lower()
