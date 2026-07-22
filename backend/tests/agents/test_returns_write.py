import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.returns_write as rw
from backend.src.agents import write_gate


def _fake_tool(name, recorder, ref="WH/IN/00057", model="stock.picking",
              res_id=156, state="assigned"):
    t = MagicMock()
    t.name = name

    async def ainvoke(args):
        recorder["args"] = args
        return json.dumps({"ok": True, "ref": ref, "model": model,
                           "res_id": res_id, "state": state, "display": "OK."},
                          ensure_ascii=False)

    t.ainvoke = ainvoke
    return t


def _graph(node):
    g = StateGraph(ERPAgentState)
    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return g.compile(checkpointer=MemorySaver())


def _state(tool, args):
    return {"messages": [], "intent": "erp_write", "confirmed": None,
            "pending_action": {"tool": tool, "args": args, "summary": "x"}}


def _ok_resolve(matches, needs):
    return {"status": "success", "data": {"matches": matches,
            "needs_disambiguation": needs}, "display": "x"}


_LAMP = [{"id": 67, "name": "Drawer Black", "score": 1}]


def _deliveries(monkeypatch, pickings, order=None):
    monkeypatch.setattr(rw.inventory, "find_done_deliveries_for_order",
                        lambda *a, **k: {
                            "status": "success",
                            "data": {"order": order or {"id": 115, "name": "S00115"},
                                     "pickings": pickings},
                            "display": "x"})


def _invoice(monkeypatch, inv):
    monkeypatch.setattr(rw.accounting, "find_posted_invoice", lambda *a, **k: {
        "status": "success", "data": {"invoice": inv}, "display": "x"})


# ── return_order coordinator ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_return_order_slot_ask(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    rec = {}
    graph = _graph(rw.make_return_order_node([_fake_tool("return_order", rec)]))
    cfg = {"configurable": {"thread_id": "ro1"}}
    res = await graph.ainvoke(_state("return_order", {}), cfg)
    assert "__interrupt__" not in res
    assert "mã đơn bán" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_return_order_no_deliveries(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _deliveries(monkeypatch, [])
    rec = {}
    graph = _graph(rw.make_return_order_node([_fake_tool("return_order", rec)]))
    cfg = {"configurable": {"thread_id": "ro2"}}
    res = await graph.ainvoke(_state("return_order", {"order_ref": "S00115"}), cfg)
    assert "__interrupt__" not in res
    assert "chưa có phiếu giao" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_return_order_full_quantity_happy(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _deliveries(monkeypatch, [{"id": 147, "name": "WH/OUT/00092",
                               "date_done": "2026-07-18 07:09:42"}])
    rec = {}
    graph = _graph(rw.make_return_order_node([_fake_tool("return_order", rec)]))
    cfg = {"configurable": {"thread_id": "ro3"}}
    res = await graph.ainvoke(_state("return_order", {"order_ref": "S00115"}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"
    assert "S00115" in itr["question"] and "WH/OUT/00092" in itr["question"]
    assert "toàn bộ" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"] == {"picking_id": 147, "lines": []}


@pytest.mark.asyncio
async def test_return_order_partial_quantity_resolves_products(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _deliveries(monkeypatch, [{"id": 147, "name": "WH/OUT/00092",
                               "date_done": "2026-07-18 07:09:42"}])
    monkeypatch.setattr(rw.inventory, "find_product",
                        lambda *a, **k: _ok_resolve(_LAMP, False))
    rec = {}
    graph = _graph(rw.make_return_order_node([_fake_tool("return_order", rec)]))
    cfg = {"configurable": {"thread_id": "ro4"}}
    res = await graph.ainvoke(_state("return_order", {
        "order_ref": "S00115",
        "lines": [{"product": "Drawer Black", "qty": 2}]}), cfg)
    itr = res["__interrupt__"][0].value
    assert "Drawer Black × 2" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"] == {"picking_id": 147,
                           "lines": [{"product_id": 67, "qty": 2.0}]}


@pytest.mark.asyncio
async def test_return_order_multiple_deliveries_disambiguates(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _deliveries(monkeypatch, [
        {"id": 147, "name": "WH/OUT/00092", "date_done": "2026-07-18 07:09:42"},
        {"id": 130, "name": "WH/OUT/00083", "date_done": "2026-07-10 09:00:00"}])
    rec = {}
    graph = _graph(rw.make_return_order_node([_fake_tool("return_order", rec)]))
    cfg = {"configurable": {"thread_id": "ro5"}}
    res = await graph.ainvoke(_state("return_order", {"order_ref": "S00115"}), cfg)
    assert res["__interrupt__"][0].value["kind"] == "disambiguation"
    res = await graph.ainvoke(Command(resume=130), cfg)
    assert res["__interrupt__"][0].value["kind"] == "confirm"
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["picking_id"] == 130


@pytest.mark.asyncio
async def test_return_order_cancel(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _deliveries(monkeypatch, [{"id": 147, "name": "WH/OUT/00092",
                               "date_done": "2026-07-18 07:09:42"}])
    rec = {}
    graph = _graph(rw.make_return_order_node([_fake_tool("return_order", rec)]))
    cfg = {"configurable": {"thread_id": "ro6"}}
    await graph.ainvoke(_state("return_order", {"order_ref": "S00115"}), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "Đã hủy" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_return_order_gate(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    graph = _graph(rw.make_return_order_node([_fake_tool("return_order", {})]))
    cfg = {"configurable": {"thread_id": "ro7"}}
    res = await graph.ainvoke(_state("return_order", {"order_ref": "S00115"}), cfg)
    assert "chưa được kích hoạt" in res["messages"][-1].content


# ── create_credit_memo coordinator ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_credit_memo_slot_ask(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    rec = {}
    graph = _graph(rw.make_create_credit_memo_node(
        [_fake_tool("create_credit_memo", rec, ref=None, model="account.move",
                    res_id=72, state="draft")]))
    cfg = {"configurable": {"thread_id": "cm1"}}
    res = await graph.ainvoke(_state("create_credit_memo", {}), cfg)
    assert "__interrupt__" not in res
    assert "số hóa đơn" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_create_credit_memo_happy(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _invoice(monkeypatch, {"id": 68, "name": "INV/2026/00017",
                           "partner_id": [15, "Azure Interior"],
                           "amount_total": 70.0})
    rec = {}
    graph = _graph(rw.make_create_credit_memo_node(
        [_fake_tool("create_credit_memo", rec, ref=None, model="account.move",
                    res_id=72, state="draft")]))
    cfg = {"configurable": {"thread_id": "cm2"}}
    res = await graph.ainvoke(_state("create_credit_memo", {
        "invoice_ref": "INV/2026/00017", "reason": "Hàng lỗi"}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"
    assert "INV/2026/00017" in itr["question"] and "Azure Interior" in itr["question"]
    assert "70" in itr["question"] and "Hàng lỗi" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"] == {"invoice_id": 68, "reason": "Hàng lỗi"}
    assert res["last_write"]["tool"] == "create_credit_memo"


@pytest.mark.asyncio
async def test_create_credit_memo_not_found(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(rw.accounting, "find_posted_invoice", lambda *a, **k: {
        "status": "error", "data": None, "display": "Không tìm thấy hóa đơn khách 'X'."})
    rec = {}
    graph = _graph(rw.make_create_credit_memo_node(
        [_fake_tool("create_credit_memo", rec)]))
    cfg = {"configurable": {"thread_id": "cm3"}}
    res = await graph.ainvoke(_state("create_credit_memo", {"invoice_ref": "X"}), cfg)
    assert "__interrupt__" not in res
    assert "Không tìm thấy" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_create_credit_memo_cancel(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _invoice(monkeypatch, {"id": 68, "name": "INV/2026/00017",
                           "partner_id": [15, "Azure Interior"], "amount_total": 70.0})
    rec = {}
    graph = _graph(rw.make_create_credit_memo_node(
        [_fake_tool("create_credit_memo", rec)]))
    cfg = {"configurable": {"thread_id": "cm4"}}
    await graph.ainvoke(_state("create_credit_memo", {"invoice_ref": "INV/2026/00017"}), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "Đã hủy" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_create_credit_memo_gate(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    graph = _graph(rw.make_create_credit_memo_node(
        [_fake_tool("create_credit_memo", {})]))
    cfg = {"configurable": {"thread_id": "cm5"}}
    res = await graph.ainvoke(_state("create_credit_memo", {"invoice_ref": "X"}), cfg)
    assert "chưa được kích hoạt" in res["messages"][-1].content


def test_returns_registered_in_registry_and_prompts():
    from backend.src.agents.write_registry import (COORDINATED_TOOLS,
                                                   WRITE_COORDINATORS, NEXT_STEPS)
    from backend.src.agents.prompts import WRITE_PLANNER_PROMPT
    assert "return_order" in COORDINATED_TOOLS
    assert "create_credit_memo" in COORDINATED_TOOLS
    assert WRITE_COORDINATORS["return_order"].node == "return_order"
    assert WRITE_COORDINATORS["create_credit_memo"].node == "create_credit_memo"
    assert NEXT_STEPS["return_order"].tool == "validate_picking"
    assert NEXT_STEPS["create_credit_memo"].tool == "post_invoice"
    assert "return_order(order_ref" in WRITE_PLANNER_PROMPT
    assert "create_credit_memo(invoice_ref" in WRITE_PLANNER_PROMPT
