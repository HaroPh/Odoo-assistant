import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from unittest.mock import MagicMock, AsyncMock
from backend.src.agents.nodes import (make_erp_write_planner_node,
                                      make_erp_write_executor_node)
from backend.src.agents import write_gate


def _tool(name, ret):
    t = MagicMock()
    t.name = name

    async def ainvoke(args):
        t.called_with = args
        return ret
    t.ainvoke = ainvoke
    return t


def _raising_tool(name="confirm_sale_order"):
    t = MagicMock()
    t.name = name

    async def ainvoke(args):
        raise RuntimeError("boom")
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
    assert (cancel["pending_action"] is None and cancel["confirmed"] is None
            and cancel["last_write"] is None)
    missing = await node({"confirmed": True,
                          "pending_action": {"tool": "ghost", "args": {}}})
    assert (missing["pending_action"] is None and missing["confirmed"] is None
            and missing["last_write"] is None)


@pytest.mark.asyncio
async def test_executor_exception_path_clears_state():
    # The except Exception branch must clear all three keys too, else a raised
    # write leaves stale pending_action/confirmed for a later turn to re-fire.
    node = make_erp_write_executor_node([_raising_tool()])
    err = await node({"confirmed": True,
                      "pending_action": {"tool": "confirm_sale_order", "args": {}}})
    assert (err["pending_action"] is None and err["confirmed"] is None
            and err["last_write"] is None)
    assert "Lỗi khi thực hiện thao tác" in err["messages"][-1].content


@pytest.mark.asyncio
async def test_planner_json_error_clears_pending_action(monkeypatch):
    # Anti-regression for the stale-action bug: a parse failure MUST clear
    # pending_action or the router re-fires the previous write unconfirmed.
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=MagicMock(content="not json"))
    node = make_erp_write_planner_node(llm)
    out = await node({"messages": []})
    assert out["pending_action"] is None


@pytest.mark.asyncio
async def test_planner_write_disabled_clears_pending_action(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    node = make_erp_write_planner_node(MagicMock())
    out = await node({"messages": []})
    assert out["pending_action"] is None


def _order_envelope(ref="S00031"):
    return json.dumps({"ok": True, "ref": ref, "model": "sale.order",
                       "res_id": 42, "state": "sale",
                       "display": f"Đã xác nhận đơn {ref}."}, ensure_ascii=False)


def _invoice_envelope():
    return json.dumps({"ok": True, "ref": None, "model": "account.move",
                       "res_id": 61, "state": "draft",
                       "display": "Đã tạo hóa đơn nháp."}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_executor_order_write_sets_working_context():
    node = make_erp_write_executor_node(
        [_tool("confirm_sale_order", _order_envelope())])
    out = await node({"confirmed": True,
                      "pending_action": {"tool": "confirm_sale_order",
                                         "args": {"order_ref": "S00031"}}})
    assert out["working_context"] == {"ref": "S00031", "model": "sale.order",
                                      "display": "Đã xác nhận đơn S00031."}


@pytest.mark.asyncio
async def test_executor_invoice_write_omits_working_context_key():
    # Invoice envelopes must NOT touch the context — key ABSENT (not None),
    # so LangGraph keeps the previous order in the channel.
    node = make_erp_write_executor_node(
        [_tool("create_invoice_from_order", _invoice_envelope())])
    out = await node({"confirmed": True,
                      "pending_action": {"tool": "create_invoice_from_order",
                                         "args": {"order_ref": "S00031"}}})
    assert "working_context" not in out


@pytest.mark.asyncio
async def test_executor_early_exits_never_wipe_working_context():
    # Anti-wipe: cancel / missing tool / exception paths must OMIT the key —
    # returning working_context=None would erase the remembered order.
    node = make_erp_write_executor_node([])
    cancel = await node({"confirmed": False, "pending_action": {"tool": "x"},
                         "working_context": {"ref": "S00031",
                                             "model": "sale.order", "display": "x"}})
    assert "working_context" not in cancel
    missing = await node({"confirmed": True,
                          "pending_action": {"tool": "ghost", "args": {}},
                          "working_context": {"ref": "S00031",
                                              "model": "sale.order", "display": "x"}})
    assert "working_context" not in missing


@pytest.mark.asyncio
async def test_executor_plain_string_tool_omits_working_context_key():
    node = make_erp_write_executor_node(
        [_tool("validate_picking", "Đã xác nhận phiếu WH/OUT/00001.")])
    out = await node({"confirmed": True,
                      "pending_action": {"tool": "validate_picking", "args": {}}})
    assert "working_context" not in out


@pytest.mark.asyncio
async def test_executor_exception_path_never_wipes_working_context():
    # Coverage-gap fix: test_executor_early_exits_never_wipe_working_context
    # only exercises cancel + missing-tool; the tool.ainvoke-raises branch
    # needs its own anti-wipe check with a pre-populated working_context.
    node = make_erp_write_executor_node([_raising_tool()])
    out = await node({"confirmed": True,
                      "pending_action": {"tool": "confirm_sale_order", "args": {}},
                      "working_context": {"ref": "S00031",
                                          "model": "sale.order", "display": "x"}})
    assert "working_context" not in out
