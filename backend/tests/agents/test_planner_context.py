import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from backend.tests.conftest import make_mock_llm
from backend.src.agents.prompts import WRITE_PLANNER_PROMPT, render_working_context
import backend.src.agents.nodes as nodes_mod
from backend.src.agents.nodes import make_erp_write_planner_node
from backend.src.agents import write_gate

WC = {"ref": "S00040", "model": "sale.order", "display": "Đã tạo báo giá S00040 (nháp)."}


class _FakeInterrupt(Exception):
    pass


def _capture_interrupt(monkeypatch):
    captured = {}

    def fake_interrupt(payload):
        captured["payload"] = payload
        raise _FakeInterrupt()

    monkeypatch.setattr(nodes_mod, "_interrupt", fake_interrupt)
    return captured


def _plan_json(tool="confirm_sale_order", order_ref="S00040"):
    return json.dumps({"tool": tool, "args": {"order_ref": order_ref},
                       "summary": "Xác nhận đơn bán"})


@pytest.mark.asyncio
async def test_planner_injects_context_as_single_system_message(monkeypatch):
    # Invariant A: ONE SystemMessage carrying render + base prompt, context first.
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _capture_interrupt(monkeypatch)
    llm = make_mock_llm(_plan_json())
    node = make_erp_write_planner_node(llm)
    with pytest.raises(_FakeInterrupt):
        await node({"messages": [HumanMessage("xác nhận đơn vừa tạo")],
                    "working_context": WC})
    sent = llm.ainvoke.await_args[0][0]
    systems = [m for m in sent if isinstance(m, SystemMessage)]
    assert len(systems) == 1
    assert systems[0].content.startswith(render_working_context(WC))
    assert WRITE_PLANNER_PROMPT in systems[0].content


@pytest.mark.asyncio
async def test_planner_without_context_uses_base_prompt_verbatim(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _capture_interrupt(monkeypatch)
    llm = make_mock_llm(_plan_json())
    node = make_erp_write_planner_node(llm)
    with pytest.raises(_FakeInterrupt):
        await node({"messages": [HumanMessage("xác nhận đơn S00040")]})
    sent = llm.ainvoke.await_args[0][0]
    systems = [m for m in sent if isinstance(m, SystemMessage)]
    assert len(systems) == 1 and systems[0].content == WRITE_PLANNER_PROMPT


@pytest.mark.asyncio
async def test_planner_explicit_ref_overrides_context_biased_plan(monkeypatch):
    # Invariant C layer 2: user names S00007; a context-biased LLM emitted S00040.
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    captured = _capture_interrupt(monkeypatch)
    llm = make_mock_llm(_plan_json(order_ref="S00040"))
    node = make_erp_write_planner_node(llm)
    with pytest.raises(_FakeInterrupt):
        await node({"messages": [HumanMessage("xác nhận đơn S00007")],
                    "working_context": WC})
    assert captured["payload"]["action"]["args"]["order_ref"] == "S00007"
    assert "S00007" in captured["payload"]["question"]


@pytest.mark.asyncio
async def test_planner_confirm_question_shows_tool_and_args(monkeypatch):
    # Invariant C layer 3: deterministic (tool: args) line, independent of summary.
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    captured = _capture_interrupt(monkeypatch)
    llm = make_mock_llm(_plan_json())
    node = make_erp_write_planner_node(llm)
    with pytest.raises(_FakeInterrupt):
        await node({"messages": [HumanMessage("xác nhận đơn vừa tạo")],
                    "working_context": WC})
    assert "(confirm_sale_order: order_ref=S00040)" in captured["payload"]["question"]
