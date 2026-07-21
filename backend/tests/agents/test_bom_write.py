import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.bom_write as bw
from backend.src.agents import write_gate


def _fake_tool(name, recorder, ref="AI-BOM", res_id=9):
    t = MagicMock()
    t.name = name

    async def ainvoke(args):
        recorder["args"] = args
        return json.dumps({"ok": True, "ref": ref, "model": "mrp.bom",
                           "res_id": res_id, "state": "active", "display": "OK."},
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
            "pending_action": {"tool": tool, "args": args, "summary": "BoM"}}


def _ok_resolve(matches, needs):
    return {"status": "success", "data": {"matches": matches,
            "needs_disambiguation": needs}, "display": "x"}


def _product(monkeypatch, matches, needs=False):
    monkeypatch.setattr(bw.inventory, "find_product",
                        lambda *a, **k: _ok_resolve(matches, needs))


def _boms(monkeypatch, boms):
    monkeypatch.setattr(bw.mrp, "find_boms_for_variant", lambda *a, **k: {
        "status": "success",
        "data": {"product": {"id": 6, "name": "Office Lamp", "tmpl_id": 6},
                 "boms": boms}, "display": "x"})


_LAMP = [{"id": 6, "name": "[FURN_8888] Office Lamp", "score": 1}]
_PRIM = {"id": 9, "code": "AI-BOM", "type": "normal", "product_qty": 1.0}


# ── create_bom coordinator ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_bom_slot_ask_combined(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    rec = {}
    graph = _graph(bw.make_create_bom_node([_fake_tool("create_bom", rec)]))
    cfg = {"configurable": {"thread_id": "cb1"}}
    res = await graph.ainvoke(_state("create_bom", {}), cfg)
    assert "__interrupt__" not in res
    msg = res["messages"][-1].content
    assert "sản phẩm" in msg and "nguyên liệu" in msg
    assert rec == {}


@pytest.mark.asyncio
async def test_create_bom_happy(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _product(monkeypatch, _LAMP)
    _boms(monkeypatch, [])          # sản phẩm chưa có BoM
    # component resolve: 2 tên khác nhau, mỗi cái đơn nghĩa. Keyed theo ref
    # (KHÔNG theo thứ tự gọi) — node LangGraph re-execute toàn bộ logic từ
    # đầu mỗi lần resume() (xem langgraph.types.interrupt docstring), nên
    # mock phải idempotent theo input, không phải 1 iterator dùng-1-lần.
    comps_by_ref = {
        "Drawer Black": _ok_resolve([{"id": 67, "name": "Drawer Black", "score": 1}], False),
        "Drawer Case Black": _ok_resolve([{"id": 68, "name": "Drawer Case Black", "score": 1}], False),
    }
    monkeypatch.setattr(bw.inventory, "find_product",
                        lambda ref, *a, **k: (_ok_resolve(_LAMP, False)
                                              if "lamp" in ref.lower() else comps_by_ref[ref]))
    rec = {}
    graph = _graph(bw.make_create_bom_node([_fake_tool("create_bom", rec)]))
    cfg = {"configurable": {"thread_id": "cb2"}}
    res = await graph.ainvoke(_state("create_bom", {
        "product_name": "Office Lamp", "batch_qty": 1,
        "components": [{"product": "Drawer Black", "qty": 2},
                       {"product": "Drawer Case Black", "qty": 1}]}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"
    assert "Office Lamp" in itr["question"]
    assert "Drawer Black × 2" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["product_id"] == 6
    assert rec["args"]["components"] == [{"product_id": 67, "qty": 2.0},
                                         {"product_id": 68, "qty": 1.0}]
    assert rec["args"]["batch_qty"] == 1.0
    assert res["last_write"]["tool"] == "create_bom"
    assert "working_context" not in res or not res.get("working_context")


@pytest.mark.asyncio
async def test_create_bom_notes_existing_boms(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _boms(monkeypatch, [{"id": 7, "code": "OLD", "type": "normal", "product_qty": 1.0}])
    monkeypatch.setattr(bw.inventory, "find_product",
                        lambda ref, *a, **k: (_ok_resolve(_LAMP, False)
                                              if "lamp" in ref.lower()
                                              else _ok_resolve([{"id": 67, "name": "Comp", "score": 1}], False)))
    rec = {}
    graph = _graph(bw.make_create_bom_node([_fake_tool("create_bom", rec)]))
    cfg = {"configurable": {"thread_id": "cb3"}}
    res = await graph.ainvoke(_state("create_bom", {
        "product_name": "Office Lamp",
        "components": [{"product": "Comp", "qty": 2}]}), cfg)
    itr = res["__interrupt__"][0].value
    assert "đã có 1 BoM" in itr["question"] and "bổ sung" in itr["question"]


@pytest.mark.asyncio
async def test_create_bom_self_component(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _boms(monkeypatch, [])
    # cả product lẫn component resolve về id 6
    monkeypatch.setattr(bw.inventory, "find_product",
                        lambda *a, **k: _ok_resolve(_LAMP, False))
    rec = {}
    graph = _graph(bw.make_create_bom_node([_fake_tool("create_bom", rec)]))
    cfg = {"configurable": {"thread_id": "cb4"}}
    res = await graph.ainvoke(_state("create_bom", {
        "product_name": "Office Lamp",
        "components": [{"product": "Office Lamp", "qty": 1}]}), cfg)
    assert "__interrupt__" not in res
    assert "chính thành phẩm" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_create_bom_cancel(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _boms(monkeypatch, [])
    monkeypatch.setattr(bw.inventory, "find_product",
                        lambda ref, *a, **k: (_ok_resolve(_LAMP, False)
                                              if "lamp" in ref.lower()
                                              else _ok_resolve([{"id": 67, "name": "Comp", "score": 1}], False)))
    rec = {}
    graph = _graph(bw.make_create_bom_node([_fake_tool("create_bom", rec)]))
    cfg = {"configurable": {"thread_id": "cb5"}}
    await graph.ainvoke(_state("create_bom", {
        "product_name": "Office Lamp",
        "components": [{"product": "Comp", "qty": 2}]}), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "Đã hủy tạo BoM" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_create_bom_gate_blocked(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    graph = _graph(bw.make_create_bom_node([_fake_tool("create_bom", {})]))
    cfg = {"configurable": {"thread_id": "cb6"}}
    res = await graph.ainvoke(_state("create_bom", {
        "product_name": "X", "components": [{"product": "Y", "qty": 1}]}), cfg)
    assert "chưa được kích hoạt" in res["messages"][-1].content


# ── update_bom_lines coordinator ─────────────────────────────────────────────

def _recipe(monkeypatch, lines):
    monkeypatch.setattr(bw.mrp, "get_bom_recipe", lambda *a, **k: {
        "status": "success",
        "data": {"bom": {"id": 9, "code": "AI-BOM", "type": "normal",
                         "product_qty": 1.0}, "lines": lines}, "display": "x"})


def _mo_count(monkeypatch, count, capped=False):
    monkeypatch.setattr(bw.mrp, "open_mo_count_for_bom", lambda *a, **k: {
        "status": "success", "data": {"count": count, "capped": capped},
        "display": "x"})


@pytest.mark.asyncio
async def test_update_bom_slot_ask(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    rec = {}
    graph = _graph(bw.make_update_bom_node([_fake_tool("update_bom_lines", rec)]))
    cfg = {"configurable": {"thread_id": "ub1"}}
    res = await graph.ainvoke(_state("update_bom_lines", {}), cfg)
    assert "__interrupt__" not in res
    assert "sản phẩm" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_update_bom_no_bom(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _product(monkeypatch, _LAMP)
    _boms(monkeypatch, [])
    rec = {}
    graph = _graph(bw.make_update_bom_node([_fake_tool("update_bom_lines", rec)]))
    cfg = {"configurable": {"thread_id": "ub2"}}
    res = await graph.ainvoke(_state("update_bom_lines", {
        "product_name": "Office Lamp",
        "changes": [{"action": "set_qty", "product": "X", "qty": 5}]}), cfg)
    assert "__interrupt__" not in res
    assert "chưa có" in res["messages"][-1].content and "BoM" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_update_bom_happy_shows_diff_and_warning(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _boms(monkeypatch, [_PRIM])
    _recipe(monkeypatch, [{"product_id": 67, "name": "Drawer Black", "qty": 2.0},
                          {"product_id": 68, "name": "Drawer Case Black", "qty": 1.0}])
    _mo_count(monkeypatch, 2)
    # product resolve -> lamp; change.product "Drawer Black" -> 67
    monkeypatch.setattr(bw.inventory, "find_product",
                        lambda ref, *a, **k: (_ok_resolve(_LAMP, False)
                                              if "lamp" in ref.lower()
                                              else _ok_resolve([{"id": 67, "name": "Drawer Black", "score": 1}], False)))
    rec = {}
    graph = _graph(bw.make_update_bom_node([_fake_tool("update_bom_lines", rec)]))
    cfg = {"configurable": {"thread_id": "ub3"}}
    res = await graph.ainvoke(_state("update_bom_lines", {
        "product_name": "Office Lamp",
        "changes": [{"action": "set_qty", "product": "Drawer Black", "qty": 5}]}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"
    assert "Hiện tại:" in itr["question"] and "Sau khi sửa:" in itr["question"]
    assert "Drawer Black × 5" in itr["question"]      # dòng sau khi sửa
    assert "⚠" in itr["question"] and "tạo TỪ SAU" in itr["question"]
    assert "2 lệnh" in itr["question"]                # count blast-radius
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["bom_id"] == 9
    assert rec["args"]["changes"] == [{"action": "set_qty", "product_id": 67, "qty": 5.0}]


@pytest.mark.asyncio
async def test_update_bom_add_existing_msg(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _boms(monkeypatch, [_PRIM])
    _recipe(monkeypatch, [{"product_id": 67, "name": "Drawer Black", "qty": 2.0}])
    _mo_count(monkeypatch, 0)
    monkeypatch.setattr(bw.inventory, "find_product",
                        lambda ref, *a, **k: (_ok_resolve(_LAMP, False)
                                              if "lamp" in ref.lower()
                                              else _ok_resolve([{"id": 67, "name": "Drawer Black", "score": 1}], False)))
    rec = {}
    graph = _graph(bw.make_update_bom_node([_fake_tool("update_bom_lines", rec)]))
    cfg = {"configurable": {"thread_id": "ub4"}}
    res = await graph.ainvoke(_state("update_bom_lines", {
        "product_name": "Office Lamp",
        "changes": [{"action": "add", "product": "Drawer Black", "qty": 3}]}), cfg)
    assert "__interrupt__" not in res
    assert "đã có" in res["messages"][-1].content
    assert rec == {}


@pytest.mark.asyncio
async def test_update_bom_multi_bom_code_selects(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _boms(monkeypatch, [_PRIM, {"id": 10, "code": "SEC", "type": "normal", "product_qty": 1.0}])
    _recipe(monkeypatch, [{"product_id": 67, "name": "Drawer Black", "qty": 2.0}])
    _mo_count(monkeypatch, 0)
    monkeypatch.setattr(bw.inventory, "find_product",
                        lambda ref, *a, **k: (_ok_resolve(_LAMP, False)
                                              if "lamp" in ref.lower()
                                              else _ok_resolve([{"id": 67, "name": "Drawer Black", "score": 1}], False)))
    rec = {}
    graph = _graph(bw.make_update_bom_node([_fake_tool("update_bom_lines", rec)]))
    cfg = {"configurable": {"thread_id": "ub5"}}
    res = await graph.ainvoke(_state("update_bom_lines", {
        "product_name": "Office Lamp", "bom_code": "sec",
        "changes": [{"action": "set_qty", "product": "Drawer Black", "qty": 5}]}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"       # KHÔNG disambiguation vì có bom_code
    await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["bom_id"] == 10


@pytest.mark.asyncio
async def test_update_bom_count_error_still_warns(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    _boms(monkeypatch, [_PRIM])
    _recipe(monkeypatch, [{"product_id": 67, "name": "Drawer Black", "qty": 2.0}])
    monkeypatch.setattr(bw.mrp, "open_mo_count_for_bom", lambda *a, **k: {
        "status": "error", "data": None, "display": "down", "error": "down"})
    monkeypatch.setattr(bw.inventory, "find_product",
                        lambda ref, *a, **k: (_ok_resolve(_LAMP, False)
                                              if "lamp" in ref.lower()
                                              else _ok_resolve([{"id": 67, "name": "Drawer Black", "score": 1}], False)))
    rec = {}
    graph = _graph(bw.make_update_bom_node([_fake_tool("update_bom_lines", rec)]))
    cfg = {"configurable": {"thread_id": "ub6"}}
    res = await graph.ainvoke(_state("update_bom_lines", {
        "product_name": "Office Lamp",
        "changes": [{"action": "set_qty", "product": "Drawer Black", "qty": 5}]}), cfg)
    itr = res["__interrupt__"][0].value
    assert "⚠" in itr["question"] and "tạo TỪ SAU" in itr["question"]
    # không có số lệnh cụ thể nhưng cảnh báo vẫn còn


def test_bom_registered_in_registry_and_prompts():
    from backend.src.agents.write_registry import (COORDINATED_TOOLS,
                                                   WRITE_COORDINATORS, NEXT_STEPS)
    from backend.src.agents.prompts import WRITE_PLANNER_PROMPT
    assert "create_bom" in COORDINATED_TOOLS
    assert "update_bom_lines" in COORDINATED_TOOLS
    assert WRITE_COORDINATORS["create_bom"].node == "create_bom"
    assert WRITE_COORDINATORS["update_bom_lines"].node == "update_bom"
    # BoM là master data — KHÔNG chain
    assert "create_bom" not in NEXT_STEPS
    assert "update_bom_lines" not in NEXT_STEPS
    assert "create_bom(product_name" in WRITE_PLANNER_PROMPT
    assert "update_bom_lines(product_name" in WRITE_PLANNER_PROMPT
