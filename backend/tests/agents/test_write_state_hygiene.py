import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from unittest.mock import MagicMock, AsyncMock
from backend.src.agents.nodes import (make_erp_write_planner_node,
                                      make_erp_write_executor_node)


def _tool(name, ret):
    t = MagicMock()
    t.name = name

    async def ainvoke(args):
        t.called_with = args
        return ret
    t.ainvoke = ainvoke
    return t


def _envelope(tool_ref="S00031", res_id=42):
    return json.dumps({"ok": True, "ref": tool_ref, "model": "sale.order",
                       "res_id": res_id, "state": "sale",
                       "display": f"Đã xác nhận đơn {tool_ref}."},
                      ensure_ascii=False)


@pytest.mark.asyncio
async def test_executor_envelope_sets_last_write_and_clears():
    node = make_erp_write_executor_node(
        [_tool("confirm_sale_order", _envelope())])
    out = await node({"confirmed": True,
                      "pending_action": {"tool": "confirm_sale_order",
                                         "args": {"order_ref": "S00031"}}})
    assert out["messages"][-1].content == "Đã xác nhận đơn S00031."
    assert out["pending_action"] is None and out["confirmed"] is None
    assert out["last_write"]["tool"] == "confirm_sale_order"
    assert out["last_write"]["ref"] == "S00031" and out["last_write"]["ok"] is True


@pytest.mark.asyncio
async def test_executor_plain_string_no_last_write():
    node = make_erp_write_executor_node(
        [_tool("validate_picking", "Đã xác nhận phiếu WH/OUT/00001.")])
    out = await node({"confirmed": True,
                      "pending_action": {"tool": "validate_picking", "args": {}}})
    assert out["last_write"] is None
    assert out["pending_action"] is None and out["confirmed"] is None


@pytest.mark.asyncio
async def test_executor_cancel_and_error_paths_clear_state():
    node = make_erp_write_executor_node([])
    cancel = await node({"confirmed": False, "pending_action": {"tool": "x"}})
    assert cancel["pending_action"] is None and cancel["last_write"] is None
    missing = await node({"confirmed": True,
                          "pending_action": {"tool": "ghost", "args": {}}})
    assert missing["pending_action"] is None and missing["last_write"] is None


@pytest.mark.asyncio
async def test_planner_json_error_clears_pending_action(monkeypatch):
    # Anti-regression for the stale-action bug: a parse failure MUST clear
    # pending_action or the router re-fires the previous write unconfirmed.
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="not json"))
    node = make_erp_write_planner_node(llm)
    out = await node({"messages": []})
    assert out["pending_action"] is None


@pytest.mark.asyncio
async def test_planner_write_disabled_clears_pending_action(monkeypatch):
    monkeypatch.delenv("WRITE_ACTIONS_ENABLED", raising=False)
    node = make_erp_write_planner_node(MagicMock())
    out = await node({"messages": []})
    assert out["pending_action"] is None
