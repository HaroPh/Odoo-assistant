import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.mrp_write as mw
from backend.src.agents import write_gate


def _fake_tool(name, recorder, display="OK."):
    t = MagicMock()
    t.name = name

    async def ainvoke(args):
        recorder["args"] = args
        return json.dumps({"ok": True, "ref": "WH/MO/00010",
                           "model": "mrp.production", "res_id": 10,
                           "state": "draft", "display": display},
                          ensure_ascii=False)

    t.ainvoke = ainvoke
    return t


def _graph(node):
    g = StateGraph(ERPAgentState)
    g.add_node("n", node)
    g.set_entry_point("n")
    g.add_edge("n", END)
    return g.compile(checkpointer=MemorySaver())


def _state(args):
    return {"messages": [], "intent": "erp_write", "confirmed": None,
            "pending_action": {"tool": "create_manufacturing_order",
                               "args": args, "summary": "MO"}}


def _ok_resolve(matches, needs):
    return {"status": "success", "data": {"matches": matches,
            "needs_disambiguation": needs}, "display": "x"}


def _one_product(monkeypatch):
    monkeypatch.setattr(mw.inventory, "find_product", lambda *a, **k: _ok_resolve(
        [{"id": 39, "name": "[FURN_8855] Drawer", "score": 1}], False))


def _boms(monkeypatch, boms):
    monkeypatch.setattr(mw.mrp, "find_boms_for_variant", lambda *a, **k: {
        "status": "success",
        "data": {"product": {"id": 39, "name": "Drawer", "tmpl_id": 24},
                 "boms": boms}, "display": "x"})


def _avail(monkeypatch, rows, all_enough=True, error=False):
    if error:
        monkeypatch.setattr(mw.mrp, "check_bom_availability", lambda *a, **k: {
            "status": "error", "data": None, "display": "down", "error": "down"})
        return
    monkeypatch.setattr(mw.mrp, "check_bom_availability", lambda *a, **k: {
        "status": "success", "data": {"rows": rows, "all_enough": all_enough},
        "display": "x"})


_PRIM = {"id": 7, "code": "PRIM-ASSEM", "type": "normal", "product_qty": 1.0}
_SEC = {"id": 8, "code": "SEC-ASSEM", "type": "normal", "product_qty": 1.0}
_KIT = {"id": 6, "code": None, "type": "phantom", "product_qty": 1.0}
_ROW_OK = {"product_id": 67, "name": "Drawer Black", "need": 2.0,
           "on_hand": 41.0, "enough": True}


@pytest.mark.asyncio
async def test_create_mo_happy_draft_then_call(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [_PRIM])
    _avail(monkeypatch, [_ROW_OK])
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m1"}}
    res = await graph.ainvoke(_state({"product_name": "Drawer", "qty": 2}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"
    assert "Drawer × 2" in itr["question"]
    assert "PRIM-ASSEM" in itr["question"]
    assert "Drawer Black × 2 (tồn 41)" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"] == {"product_id": 39, "qty": 2.0, "bom_id": 7}
    assert res["last_write"]["tool"] == "create_manufacturing_order"
    assert res["last_write"]["res_id"] == 10
    assert "working_context" not in res or not res.get("working_context")


@pytest.mark.asyncio
async def test_create_mo_slot_ask_combined(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m2"}}
    res = await graph.ainvoke(_state({}), cfg)
    assert "__interrupt__" not in res
    msg = res["messages"][-1].content
    assert "sản phẩm cần sản xuất" in msg and "số lượng" in msg
    assert rec == {}


@pytest.mark.asyncio
async def test_create_mo_product_ambiguous_disambig(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(mw.inventory, "find_product", lambda *a, **k: _ok_resolve(
        [{"id": 39, "name": "Drawer A", "score": .6},
         {"id": 40, "name": "Drawer B", "score": .6}], True))
    _boms(monkeypatch, [_PRIM])
    _avail(monkeypatch, [_ROW_OK])
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m3"}}
    res = await graph.ainvoke(_state({"product_name": "Drawer", "qty": 2}), cfg)
    assert res["__interrupt__"][0].value["kind"] == "disambiguation"
    res = await graph.ainvoke(Command(resume=40), cfg)
    assert res["__interrupt__"][0].value["kind"] == "confirm"
    await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["product_id"] == 40


@pytest.mark.asyncio
async def test_create_mo_no_bom_msg(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [])
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m4"}}
    res = await graph.ainvoke(_state({"product_name": "Drawer", "qty": 2}), cfg)
    assert "__interrupt__" not in res
    assert "chưa có định mức" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_create_mo_kit_only_msg(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [_KIT])
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m5"}}
    res = await graph.ainvoke(_state({"product_name": "Table Kit", "qty": 2}), cfg)
    assert "__interrupt__" not in res
    assert "Kit" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_create_mo_multi_bom_disambig(monkeypatch):
    # Case THẬT trên instance: Drawer có 2 BoM normal (PRIM-ASSEM + SEC-ASSEM)
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [_PRIM, _SEC])
    _avail(monkeypatch, [_ROW_OK])
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m6"}}
    res = await graph.ainvoke(_state({"product_name": "Drawer", "qty": 2}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "disambiguation"
    assert any("SEC-ASSEM" in o["name"] for o in itr["options"])
    res = await graph.ainvoke(Command(resume=8), cfg)
    assert res["__interrupt__"][0].value["kind"] == "confirm"
    await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["bom_id"] == 8


@pytest.mark.asyncio
async def test_create_mo_bom_code_selects_without_interrupt(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [_PRIM, _SEC])
    _avail(monkeypatch, [_ROW_OK])
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m7"}}
    res = await graph.ainvoke(_state({"product_name": "Drawer", "qty": 2,
                                      "bom_code": "sec-assem"}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"            # KHÔNG disambiguation
    await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["bom_id"] == 8          # casefold match


@pytest.mark.asyncio
async def test_create_mo_bom_code_wrong_lists_codes(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [_PRIM, _SEC])
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m8"}}
    res = await graph.ainvoke(_state({"product_name": "Drawer", "qty": 2,
                                      "bom_code": "XYZ"}), cfg)
    assert "__interrupt__" not in res
    msg = res["messages"][-1].content
    assert "PRIM-ASSEM" in msg and "SEC-ASSEM" in msg
    assert rec == {}


@pytest.mark.asyncio
async def test_create_mo_draft_shows_shortage(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [_PRIM])
    _avail(monkeypatch,
           [{"product_id": 67, "name": "Drawer Black", "need": 500.0,
             "on_hand": 41.0, "enough": False}], all_enough=False)
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m9"}}
    res = await graph.ainvoke(_state({"product_name": "Drawer", "qty": 500}), cfg)
    itr = res["__interrupt__"][0].value
    assert "THIẾU" in itr["question"]
    assert "⚠ Thiếu nguyên liệu" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)   # vẫn tạo được nháp
    assert rec["args"]["qty"] == 500.0


@pytest.mark.asyncio
async def test_create_mo_availability_error_still_creates(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [_PRIM])
    _avail(monkeypatch, None, error=True)
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m10"}}
    res = await graph.ainvoke(_state({"product_name": "Drawer", "qty": 2}), cfg)
    itr = res["__interrupt__"][0].value
    assert "không kiểm tra được tồn kho nguyên liệu" in itr["question"]
    await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["bom_id"] == 7


@pytest.mark.asyncio
async def test_create_mo_cancel(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [_PRIM])
    _avail(monkeypatch, [_ROW_OK])
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m11"}}
    await graph.ainvoke(_state({"product_name": "Drawer", "qty": 2}), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "Đã hủy tạo lệnh sản xuất" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_create_mo_shows_chain_note(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _one_product(monkeypatch)
    _boms(monkeypatch, [_PRIM])
    _avail(monkeypatch, [_ROW_OK])
    rec = {}
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", rec)]))
    cfg = {"configurable": {"thread_id": "m12"}}
    state = _state({"product_name": "Drawer", "qty": 2})
    state["pending_action"]["chain_note"] = "\n\nSau đó tự động: Xác nhận lệnh sản xuất"
    res = await graph.ainvoke(state, cfg)
    assert "Sau đó tự động: Xác nhận lệnh sản xuất" in res["__interrupt__"][0].value["question"]


@pytest.mark.asyncio
async def test_create_mo_gate_blocked(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    graph = _graph(mw.make_create_mo_node([_fake_tool("create_manufacturing_order", {})]))
    cfg = {"configurable": {"thread_id": "m13"}}
    res = await graph.ainvoke(_state({"product_name": "Drawer", "qty": 2}), cfg)
    assert "chưa được kích hoạt" in res["messages"][-1].content


def test_mrp_registered_in_registry_and_prompts():
    from backend.src.agents.write_registry import (COORDINATED_TOOLS,
                                                   WRITE_COORDINATORS, NEXT_STEPS)
    from backend.src.agents.prompts import WRITE_PLANNER_PROMPT, SYSTEM_PROMPT
    assert "create_manufacturing_order" in COORDINATED_TOOLS
    assert WRITE_COORDINATORS["create_manufacturing_order"].node == "create_mo"
    assert NEXT_STEPS["create_manufacturing_order"].tool == "confirm_manufacturing_order"
    assert NEXT_STEPS["confirm_manufacturing_order"].tool == "complete_manufacturing_order"
    assert "complete_manufacturing_order" not in NEXT_STEPS      # terminal
    for name in ("create_manufacturing_order", "confirm_manufacturing_order",
                 "complete_manufacturing_order"):
        assert name in WRITE_PLANNER_PROMPT
    assert "get_bom_detail" in SYSTEM_PROMPT
    assert "list_manufacturing_orders" in SYSTEM_PROMPT
