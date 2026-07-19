import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from unittest.mock import MagicMock
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.state import ERPAgentState
import backend.src.agents.crm_write as cw
from backend.src.agents import write_gate


def _fake_tool(name, recorder, display="OK."):
    t = MagicMock()
    t.name = name

    async def ainvoke(args):
        recorder["args"] = args
        return json.dumps({"ok": True, "ref": "Lead X", "model": "crm.lead",
                           "res_id": 45, "state": "lead", "display": display},
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
            "pending_action": {"tool": tool, "args": args, "summary": "CRM"}}


def _ok_resolve(matches, needs):
    return {"status": "success", "data": {"matches": matches,
            "needs_disambiguation": needs}, "display": "x"}


def _no_dups(*a, **k):
    return {"status": "success", "data": {"rows": []}, "display": "Không trùng."}


# ── create_lead coordinator ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_lead_happy_draft_then_call(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(cw.crm, "find_lead_duplicates", _no_dups)
    rec = {}
    graph = _graph(cw.make_create_lead_node([_fake_tool("create_lead", rec)]))
    cfg = {"configurable": {"thread_id": "c1"}}
    res = await graph.ainvoke(_state("create_lead",
                                     {"name": "Quan tâm lốp 18inch",
                                      "contact_name": "Trần Phúc",
                                      "phone": "0901234567"}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"
    assert "Trần Phúc" in itr["question"] and "0901234567" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["name"] == "Quan tâm lốp 18inch"
    assert rec["args"]["contact_name"] == "Trần Phúc"
    # last_write phải set để NEXT_STEPS chain create_lead→convert_lead chạy
    assert res["last_write"]["tool"] == "create_lead"
    assert res["last_write"]["res_id"] == 45


@pytest.mark.asyncio
async def test_create_lead_missing_all_contact_asks_once(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    rec = {}
    graph = _graph(cw.make_create_lead_node([_fake_tool("create_lead", rec)]))
    cfg = {"configurable": {"thread_id": "c2"}}
    res = await graph.ainvoke(_state("create_lead", {"name": "Lead mới"}), cfg)
    assert "__interrupt__" not in res              # slot-fill = _msg, KHÔNG interrupt
    assert "liên hệ" in res["messages"][-1].content.lower()
    assert rec == {}


@pytest.mark.asyncio
async def test_create_lead_derives_title_when_missing(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(cw.crm, "find_lead_duplicates", _no_dups)
    rec = {}
    graph = _graph(cw.make_create_lead_node([_fake_tool("create_lead", rec)]))
    cfg = {"configurable": {"thread_id": "c3"}}
    res = await graph.ainvoke(_state("create_lead",
                                     {"contact_name": "Trần Phúc"}), cfg)
    itr = res["__interrupt__"][0].value
    assert "Lead: Trần Phúc" in itr["question"]
    await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["name"] == "Lead: Trần Phúc"


@pytest.mark.asyncio
async def test_create_lead_dup_warning_shown_not_blocking(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(cw.crm, "find_lead_duplicates",
                        lambda *a, **k: {"status": "success",
                                         "data": {"rows": [{"name": "Lead cũ",
                                                            "type": "lead"}]},
                                         "display": "1 lead trùng."})
    rec = {}
    graph = _graph(cw.make_create_lead_node([_fake_tool("create_lead", rec)]))
    cfg = {"configurable": {"thread_id": "c4"}}
    res = await graph.ainvoke(_state("create_lead",
                                     {"name": "X", "email": "a@b.com"}), cfg)
    itr = res["__interrupt__"][0].value
    assert "⚠" in itr["question"] and "Lead cũ" in itr["question"]
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["name"] == "X"              # vẫn tạo được sau confirm


@pytest.mark.asyncio
async def test_create_lead_shows_chain_note_in_confirm(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(cw.crm, "find_lead_duplicates", _no_dups)
    rec = {}
    graph = _graph(cw.make_create_lead_node([_fake_tool("create_lead", rec)]))
    cfg = {"configurable": {"thread_id": "c6"}}
    state = _state("create_lead", {"name": "X", "phone": "0901"})
    state["pending_action"]["chain_note"] = "\n\nSau đó tự động: Chuyển thành cơ hội"
    res = await graph.ainvoke(state, cfg)
    itr = res["__interrupt__"][0].value
    # Invariant-C-style: câu confirm đầu chuỗi hiện toàn bộ chuỗi khai báo
    assert "Sau đó tự động: Chuyển thành cơ hội" in itr["question"]


@pytest.mark.asyncio
async def test_create_lead_cancel(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(cw.crm, "find_lead_duplicates", _no_dups)
    rec = {}
    graph = _graph(cw.make_create_lead_node([_fake_tool("create_lead", rec)]))
    cfg = {"configurable": {"thread_id": "c5"}}
    await graph.ainvoke(_state("create_lead",
                               {"name": "X", "phone": "09"}), cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert "hủy" in res["messages"][-1].content.lower()
    assert rec == {}


# ── convert_lead coordinator ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_convert_lead_happy(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(cw.crm, "find_lead", lambda *a, **k: _ok_resolve(
        [{"id": 45, "name": "Quan tâm lốp", "score": 1}], False))
    rec = {}
    graph = _graph(cw.make_convert_lead_node([_fake_tool("convert_lead", rec)]))
    cfg = {"configurable": {"thread_id": "v1"}}
    res = await graph.ainvoke(_state("convert_lead",
                                     {"lead_ref": "Quan tâm lốp",
                                      "assignee": "Marc Demo"}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm"
    assert "Quan tâm lốp" in itr["question"] and "Marc Demo" in itr["question"]
    await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"] == {"lead_id": 45, "assignee_name": "Marc Demo"}


@pytest.mark.asyncio
async def test_convert_lead_ambiguous_disambig(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(cw.crm, "find_lead", lambda *a, **k: _ok_resolve(
        [{"id": 45, "name": "Lead A", "score": .6},
         {"id": 46, "name": "Lead B", "score": .6}], True))
    rec = {}
    graph = _graph(cw.make_convert_lead_node([_fake_tool("convert_lead", rec)]))
    cfg = {"configurable": {"thread_id": "v2"}}
    res = await graph.ainvoke(_state("convert_lead", {"lead_ref": "Lead"}), cfg)
    assert res["__interrupt__"][0].value["kind"] == "disambiguation"
    res = await graph.ainvoke(Command(resume=46), cfg)
    assert res["__interrupt__"][0].value["kind"] == "confirm"
    await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["lead_id"] == 46


@pytest.mark.asyncio
async def test_convert_lead_missing_ref_asks(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    rec = {}
    graph = _graph(cw.make_convert_lead_node([_fake_tool("convert_lead", rec)]))
    cfg = {"configurable": {"thread_id": "v3"}}
    res = await graph.ainvoke(_state("convert_lead", {}), cfg)
    assert "__interrupt__" not in res
    assert "lead" in res["messages"][-1].content.lower()
    assert rec == {}


# ── log_activity coordinator ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_activity_slot_ask_combined(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    rec = {}
    graph = _graph(cw.make_log_activity_node([_fake_tool("log_activity", rec)]))
    cfg = {"configurable": {"thread_id": "a1"}}
    res = await graph.ainvoke(_state("log_activity",
                                     {"lead_ref": "Quan tâm lốp"}), cfg)
    assert "__interrupt__" not in res
    msg = res["messages"][-1].content.lower()
    # hỏi GỘP cả 2 slot thiếu trong MỘT câu
    assert "loại hoạt động" in msg and "nội dung" in msg
    assert rec == {}


@pytest.mark.asyncio
async def test_log_activity_alias_goi_dien_maps_to_call(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(cw.crm, "find_lead", lambda *a, **k: _ok_resolve(
        [{"id": 45, "name": "Cơ hội A", "score": 1}], False))
    rec = {}
    graph = _graph(cw.make_log_activity_node([_fake_tool("log_activity", rec)]))
    cfg = {"configurable": {"thread_id": "a2"}}
    res = await graph.ainvoke(_state("log_activity",
                                     {"lead_ref": "Cơ hội A",
                                      "activity_type": "gọi điện",
                                      "summary": "Tư vấn thông số"}), cfg)
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "confirm" and "Call" in itr["question"]
    await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["activity_type"] == "Call"
    assert rec["args"]["date_deadline"]            # default hôm nay đã điền


@pytest.mark.asyncio
async def test_log_activity_invalid_type_lists_options(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    rec = {}
    graph = _graph(cw.make_log_activity_node([_fake_tool("log_activity", rec)]))
    cfg = {"configurable": {"thread_id": "a3"}}
    res = await graph.ainvoke(_state("log_activity",
                                     {"lead_ref": "X", "activity_type": "karaoke",
                                      "summary": "y"}), cfg)
    assert "__interrupt__" not in res
    msg = res["messages"][-1].content
    assert "Call" in msg and "Meeting" in msg
    assert rec == {}


@pytest.mark.asyncio
async def test_gate_blocks_all_three(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    for maker, tool in ((cw.make_create_lead_node, "create_lead"),
                        (cw.make_convert_lead_node, "convert_lead"),
                        (cw.make_log_activity_node, "log_activity")):
        graph = _graph(maker([_fake_tool(tool, {})]))
        cfg = {"configurable": {"thread_id": f"g-{tool}"}}
        res = await graph.ainvoke(_state(tool, {"name": "x", "lead_ref": "x"}), cfg)
        assert "chưa được kích hoạt" in res["messages"][-1].content
