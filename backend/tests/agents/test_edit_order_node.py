import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.edit_order as eo


def _env_tool(name, capture):
    t = MagicMock(); t.name = name

    async def ainvoke(args):
        capture["name"] = name; capture["args"] = args
        return json.dumps({"ok": True, "ref": args.get("order_ref", "S00040"),
                           "model": "sale.order", "res_id": 40, "state": "draft",
                           "display": f"Đã sửa báo giá {args.get('order_ref')}."},
                          ensure_ascii=False)

    t.ainvoke = ainvoke
    return t


def _graph(node, node_name="edit_order"):
    g = StateGraph(ERPAgentState)
    g.add_node(node_name, node)
    g.set_entry_point(node_name)
    g.add_edge(node_name, END)
    return g.compile(checkpointer=MemorySaver())


def _state(changes, order_ref="S00040"):
    return {"messages": [], "intent": "erp_write", "confirmed": None,
            "pending_action": {"tool": "update_quotation_lines",
                               "args": {"order_ref": order_ref, "changes": changes}}}


def _detail(state="draft", lines=None):
    lines = lines if lines is not None else [
        {"id": 101, "product_id": [552, "Large Cabinet"], "product_uom_qty": 2.0,
         "price_unit": 100000.0, "price_subtotal": 200000.0}]
    return {"status": "success", "display": "x",
            "data": {"order": {"id": 40, "name": "S00040",
                               "partner_id": [41, "Azur"], "state": state},
                     "lines": lines}}


@pytest.mark.asyncio
async def test_write_disabled_gate(monkeypatch):
    monkeypatch.delenv("WRITE_ACTIONS_ENABLED", raising=False)
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", {})], eo.SALE_EDIT_CFG)
    res = await _graph(node).ainvoke(
        _state([{"action": "set_qty", "product": "Large Cabinet", "qty": 5}]),
        {"configurable": {"thread_id": "d1"}})
    assert "chưa được kích hoạt" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_empty_changes(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", {})], eo.SALE_EDIT_CFG)
    res = await _graph(node).ainvoke(_state([]), {"configurable": {"thread_id": "d2"}})
    assert "sửa gì" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_order_not_found(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail",
                        lambda ref: {"status": "error", "display": "Không tìm thấy đơn 'S0'."})
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", {})], eo.SALE_EDIT_CFG)
    res = await _graph(node).ainvoke(
        _state([{"action": "remove", "product": "X"}]), {"configurable": {"thread_id": "d3"}})
    assert "Không tìm thấy" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_set_qty_happy_path(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail())
    cap = {}
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", cap)], eo.SALE_EDIT_CFG)
    graph = _graph(node); cfg = {"configurable": {"thread_id": "d4"}}
    res = await graph.ainvoke(
        _state([{"action": "set_qty", "product": "Large Cabinet", "qty": 5}]), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm" and "→ 5" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert cap["args"]["ops"] == [{"op": "set_qty", "line_id": 101, "qty": 5}]
    assert res["last_write"]["tool"] == "update_quotation_lines"
    assert res["working_context"]["ref"] == "S00040"
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_remove_line_match(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail())
    cap = {}
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", cap)], eo.SALE_EDIT_CFG)
    graph = _graph(node); cfg = {"configurable": {"thread_id": "d5"}}
    await graph.ainvoke(_state([{"action": "remove", "product": "large cabinet"}]), cfg)
    await graph.ainvoke(Command(resume=True), cfg)
    assert cap["args"]["ops"] == [{"op": "remove", "line_id": 101}]


@pytest.mark.asyncio
async def test_add_resolves_product(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail())
    monkeypatch.setattr(eo.inventory, "find_product", lambda *a, **k: {
        "status": "success", "display": "x",
        "data": {"matches": [{"id": 900, "name": "Desk Pad", "score": 1}],
                 "needs_disambiguation": False}})
    monkeypatch.setattr(eo.sales, "get_product_price",
                        lambda *a, **k: {"status": "success", "data": {"price": 50000.0}})
    cap = {}
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", cap)], eo.SALE_EDIT_CFG)
    graph = _graph(node); cfg = {"configurable": {"thread_id": "d6"}}
    res = await graph.ainvoke(
        _state([{"action": "add", "product": "Desk Pad", "qty": 3}]), cfg)
    assert "Desk Pad" in res["__interrupt__"][0].value["question"]
    await graph.ainvoke(Command(resume=True), cfg)
    assert cap["args"]["ops"] == [{"op": "add", "product_id": 900, "qty": 3}]


@pytest.mark.asyncio
async def test_remove_no_match_terminal(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail())
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", {})], eo.SALE_EDIT_CFG)
    res = await _graph(node).ainvoke(
        _state([{"action": "remove", "product": "Nonexistent"}]),
        {"configurable": {"thread_id": "d7"}})
    assert "__interrupt__" not in res
    assert "Không tìm thấy dòng" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_multi_match_line_disambiguation(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    lines = [{"id": 101, "product_id": [552, "Cabinet"], "product_uom_qty": 2.0,
              "price_unit": 1.0, "price_subtotal": 2.0},
             {"id": 102, "product_id": [552, "Cabinet"], "product_uom_qty": 7.0,
              "price_unit": 1.0, "price_subtotal": 7.0}]
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail(lines=lines))
    cap = {}
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", cap)], eo.SALE_EDIT_CFG)
    graph = _graph(node); cfg = {"configurable": {"thread_id": "d8"}}
    res = await graph.ainvoke(_state([{"action": "remove", "product": "Cabinet"}]), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "disambiguation"
    assert {o["id"] for o in itr["options"]} == {101, 102}
    res = await graph.ainvoke(Command(resume=102), cfg)  # pick the SL-7 line
    await graph.ainvoke(Command(resume=True), cfg)
    assert cap["args"]["ops"] == [{"op": "remove", "line_id": 102}]


@pytest.mark.asyncio
async def test_confirmed_order_offers_flag_note(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail(state="sale"))
    cap = {}
    flag = MagicMock(); flag.name = "flag_order_for_review"

    async def flag_invoke(args):
        cap["name"] = "flag_order_for_review"; cap["args"] = args
        return json.dumps({"ok": True, "ref": "S00040", "model": "sale.order",
                           "res_id": 40, "state": "sale",
                           "display": "Đã ghi chú nội bộ trên đơn S00040."},
                          ensure_ascii=False)

    flag.ainvoke = flag_invoke
    node = eo.make_edit_order_node([flag], eo.SALE_EDIT_CFG)
    graph = _graph(node); cfg = {"configurable": {"thread_id": "d9"}}
    res = await graph.ainvoke(
        _state([{"action": "set_qty", "product": "Large Cabinet", "qty": 5}]), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm" and "đã xác nhận" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert cap["args"]["model"] == "sale.order"
    assert "đổi sl" in cap["args"]["note"].lower()
    assert res["last_write"]["tool"] == "flag_order_for_review"


@pytest.mark.asyncio
async def test_confirmed_order_decline_flag(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail(state="sale"))
    flag = MagicMock(); flag.name = "flag_order_for_review"
    called = []
    async def flag_invoke(args): called.append(args); return "{}"
    flag.ainvoke = flag_invoke
    node = eo.make_edit_order_node([flag], eo.SALE_EDIT_CFG)
    graph = _graph(node); cfg = {"configurable": {"thread_id": "d10"}}
    await graph.ainvoke(_state([{"action": "remove", "product": "Large Cabinet"}]), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "hủy" in res["messages"][-1].content.lower()
    assert called == []


@pytest.mark.asyncio
async def test_cancel_at_diff_confirm(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail())
    cap = {}
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", cap)], eo.SALE_EDIT_CFG)
    graph = _graph(node); cfg = {"configurable": {"thread_id": "d11"}}
    await graph.ainvoke(_state([{"action": "set_qty", "product": "Large Cabinet", "qty": 5}]), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "hủy" in res["messages"][-1].content.lower()
    assert cap == {}


@pytest.mark.asyncio
async def test_set_qty_zero_rejected(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail())
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", {})], eo.SALE_EDIT_CFG)
    res = await _graph(node).ainvoke(
        _state([{"action": "set_qty", "product": "Large Cabinet", "qty": 0}]),
        {"configurable": {"thread_id": "d12"}})
    assert "lớn hơn 0" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_add_string_qty_rejected_no_crash(monkeypatch):
    # A local LLM can emit qty as a JSON string (e.g. "5"). Must not raise
    # TypeError from `"5" <= 0`; must return a friendly Vietnamese message.
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail())
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", {})], eo.SALE_EDIT_CFG)
    res = await _graph(node).ainvoke(
        _state([{"action": "add", "product": "Desk Pad", "qty": "5"}]),
        {"configurable": {"thread_id": "d14"}})
    assert "__interrupt__" not in res
    assert "phải là số" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_set_qty_string_qty_rejected_no_crash(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail())
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", {})], eo.SALE_EDIT_CFG)
    res = await _graph(node).ainvoke(
        _state([{"action": "set_qty", "product": "Large Cabinet", "qty": "9"}]),
        {"configurable": {"thread_id": "d15"}})
    assert "__interrupt__" not in res
    assert "phải là số" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_add_none_qty_rejected_no_crash(monkeypatch):
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    monkeypatch.setattr(eo.sales, "get_sale_order_detail", lambda ref: _detail())
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", {})], eo.SALE_EDIT_CFG)
    res = await _graph(node).ainvoke(
        _state([{"action": "add", "product": "Desk Pad", "qty": None}]),
        {"configurable": {"thread_id": "d16"}})
    assert "__interrupt__" not in res
    assert "phải là số" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_write_disabled_never_wipes_working_context(monkeypatch):
    # Direct node call (not through graph) so an omitted key is ABSENT in the
    # raw return — the node-level omit-vs-None contract (see PR #7).
    monkeypatch.delenv("WRITE_ACTIONS_ENABLED", raising=False)
    node = eo.make_edit_order_node([_env_tool("update_quotation_lines", {})], eo.SALE_EDIT_CFG)
    state = _state([{"action": "remove", "product": "X"}])
    state["working_context"] = {"ref": "S00031", "model": "sale.order", "display": "x"}
    out = await node(state)
    assert "working_context" not in out


@pytest.mark.asyncio
async def test_rfq_uses_purchase_detail_and_no_price(monkeypatch):
    # Purchase cfg: get_detail hits purchase, qty_field product_qty, price=False.
    monkeypatch.setenv("WRITE_ACTIONS_ENABLED", "true")
    purch_detail = {"status": "success", "display": "x",
                    "data": {"order": {"id": 8, "name": "P00008",
                                       "partner_id": [70, "ACME"], "state": "draft"},
                             "lines": [{"id": 201, "product_id": [553, "Bàn"],
                                        "product_qty": 4.0, "price_unit": 1.0,
                                        "price_subtotal": 4.0}]}}
    monkeypatch.setattr(eo.purchase, "get_purchase_order_detail", lambda ref: purch_detail)
    cap = {}
    tool = MagicMock(); tool.name = "update_rfq_lines"
    async def ainvoke(args):
        cap["args"] = args
        return json.dumps({"ok": True, "ref": "P00008", "model": "purchase.order",
                           "res_id": 8, "state": "draft", "display": "Đã sửa đơn mua P00008."},
                          ensure_ascii=False)
    tool.ainvoke = ainvoke
    node = eo.make_edit_order_node([tool], eo.PURCHASE_EDIT_CFG)
    graph = _graph(node, "edit_rfq"); cfg = {"configurable": {"thread_id": "d13"}}
    st = _state([{"action": "set_qty", "product": "Bàn", "qty": 9}], order_ref="P00008")
    st["pending_action"]["tool"] = "update_rfq_lines"
    await graph.ainvoke(st, cfg)
    await graph.ainvoke(Command(resume=True), cfg)
    assert cap["args"]["ops"] == [{"op": "set_qty", "line_id": 201, "qty": 9}]
