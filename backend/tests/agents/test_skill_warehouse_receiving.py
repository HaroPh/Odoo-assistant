# backend/tests/agents/test_skill_warehouse_receiving.py
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.skill_warehouse_receiving as swr
from backend.src.agents import write_gate


def test_parse_qty_plain_number():
    assert swr.parse_qty("45") == 45.0


def test_parse_qty_vietnamese_thousand_separator():
    assert swr.parse_qty("đã đếm được 1.000 cái") == 1000.0


def test_parse_qty_comma_thousand_separator():
    assert swr.parse_qty("1,000") == 1000.0


def test_parse_qty_decimal_comma():
    assert swr.parse_qty("12,5") == 12.5


def test_parse_qty_no_number_returns_none():
    assert swr.parse_qty("chưa đếm xong") is None


def test_parse_qty_non_string_returns_none():
    assert swr.parse_qty(False) is None
    assert swr.parse_qty(None) is None


def test_parse_qty_ignores_digits_inside_po_code_before_qty():
    # PO code digits ("00003") must not be mistaken for the quantity that
    # follows later in the same reply.
    assert swr.parse_qty("đơn P00003 nhận 45") == 45.0


def test_parse_qty_ignores_digits_inside_po_code_before_qty_with_comma():
    assert swr.parse_qty("P00003, thực nhận 45 cái") == 45.0


def _fake_tools(receive_ret=None, flag_ret=None):
    receive = MagicMock()
    receive.name = "receive_order"
    flag = MagicMock()
    flag.name = "flag_order_for_review"
    calls = {}

    async def receive_ainvoke(args):
        calls["receive_args"] = args
        return receive_ret or ('{"ok": true, "ref": "P00003", "model": "purchase.order", '
                               '"res_id": 9, "state": "purchase", '
                               '"display": "Đã nhận hàng cho đơn mua P00003 (1 phiếu)."}')
    receive.ainvoke = receive_ainvoke

    async def flag_ainvoke(args):
        calls["flag_args"] = args
        return flag_ret or ('{"ok": true, "ref": "P00003", "model": "purchase.order", '
                            '"res_id": 9, "state": "purchase", '
                            '"display": "Đã ghi chú nội bộ trên đơn P00003."}')
    flag.ainvoke = flag_ainvoke

    return [receive, flag], calls


def _graph(node):
    g = StateGraph(ERPAgentState)
    g.add_node("warehouse_receiving", node)
    g.set_entry_point("warehouse_receiving")
    g.add_edge("warehouse_receiving", END)
    return g.compile(checkpointer=MemorySaver())


def _state(po_ref):
    return {"messages": [], "pending_action": {"tool": "skill:warehouse_receiving",
            "args": {"po_ref": po_ref}}}


def _po_detail(qty=10.0, found=True, qtys=None):
    if not found:
        return {"status": "error", "data": None, "display": "Không tìm thấy đơn mua 'P00003'.",
                "error": "not found"}
    if qtys is not None:
        lines = [{"product_id": [i + 1, f"SP{i + 1}"], "product_qty": q}
                  for i, q in enumerate(qtys)]
    else:
        lines = [{"product_id": [1, "Tủ"], "product_qty": qty}]
    return {"status": "success",
            "data": {"order": {"id": 9, "name": "P00003"}, "lines": lines},
            "display": "x"}


@pytest.mark.asyncio
async def test_happy_path_match_and_qc_pass_receives(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(swr.purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qty=10.0))
    tools, calls = _fake_tools()
    node = swr.make_node(tools)
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "w1"}}

    res = await graph.ainvoke(_state("P00003"), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "free_text" and "kiểm đếm" in itr["question"]

    res = await graph.ainvoke(Command(resume="10"), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "disambiguation" and "QC" in itr["question"]

    res = await graph.ainvoke(Command(resume="pass"), cfg)
    assert "P00003" in res["messages"][-1].content
    assert calls["receive_args"] == {"order_ref": "P00003"}
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_multi_line_float_sum_matches_exactly_not_flagged(monkeypatch):
    # sum([2.1, 2.2, 3.3]) == 7.6000000000000005 in raw IEEE-754 float, but a
    # worker who correctly counts and reports "7.6" must be treated as an
    # exact match (proceeds to QC / receive_order), not flagged short/excess.
    assert sum([2.1, 2.2, 3.3]) != 7.6  # sanity: the float noise is real
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(swr.purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qtys=[2.1, 2.2, 3.3]))
    tools, calls = _fake_tools()
    node = swr.make_node(tools)
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "w1b"}}

    res = await graph.ainvoke(_state("P00003"), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "free_text" and "kiểm đếm" in itr["question"]

    res = await graph.ainvoke(Command(resume="7.6"), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "disambiguation" and "QC" in itr["question"]
    assert "flag_args" not in calls

    res = await graph.ainvoke(Command(resume="pass"), cfg)
    assert "P00003" in res["messages"][-1].content
    assert calls["receive_args"] == {"order_ref": "P00003"}
    assert "flag_args" not in calls
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_short_quantity_flags_instead_of_receiving(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(swr.purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qty=10.0))
    tools, calls = _fake_tools()
    node = swr.make_node(tools)
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "w2"}}

    await graph.ainvoke(_state("P00003"), cfg)
    res = await graph.ainvoke(Command(resume="7"), cfg)
    assert "thiếu hàng" in res["messages"][-1].content
    assert "receive_args" not in calls
    assert calls["flag_args"]["order_ref"] == "P00003"
    assert "thực nhận 7" in calls["flag_args"]["note"]
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_excess_quantity_flags_instead_of_receiving(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(swr.purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qty=10.0))
    tools, calls = _fake_tools()
    node = swr.make_node(tools)
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "w3"}}

    await graph.ainvoke(_state("P00003"), cfg)
    res = await graph.ainvoke(Command(resume="15"), cfg)
    assert "nhận thừa" in res["messages"][-1].content
    assert "receive_args" not in calls
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_qc_fail_does_not_receive(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(swr.purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qty=10.0))
    tools, calls = _fake_tools()
    node = swr.make_node(tools)
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "w4"}}

    await graph.ainvoke(_state("P00003"), cfg)
    await graph.ainvoke(Command(resume="10"), cfg)
    res = await graph.ainvoke(Command(resume="fail"), cfg)
    assert "không đạt" in res["messages"][-1].content
    assert "receive_args" not in calls
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_po_not_found(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(swr.purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(found=False))
    tools, calls = _fake_tools()
    node = swr.make_node(tools)
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "w5"}}

    await graph.ainvoke(_state("P00003"), cfg)
    res = await graph.ainvoke(Command(resume="10"), cfg)
    assert "Không tìm thấy" in res["messages"][-1].content
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_unparseable_quantity_asks_again_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    tools, calls = _fake_tools()
    node = swr.make_node(tools)
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "w6"}}

    await graph.ainvoke(_state("P00003"), cfg)
    res = await graph.ainvoke(Command(resume="chưa đếm xong"), cfg)
    assert "một con số" in res["messages"][-1].content
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_write_gate_off_blocks_before_any_checkpoint(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    node = swr.make_node([])
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "w7"}}
    res = await graph.ainvoke(_state("P00003"), cfg)
    assert "chưa được kích hoạt" in res["messages"][-1].content
    assert res["pending_action"] is None


@pytest.mark.asyncio
async def test_missing_po_ref_asks_instead_of_guessing(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    node = swr.make_node([])
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "w8"}}
    res = await graph.ainvoke(_state(""), cfg)
    assert "mã đơn mua" in res["messages"][-1].content
    assert res["pending_action"] is None
