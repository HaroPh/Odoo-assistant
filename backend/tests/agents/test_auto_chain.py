# backend/tests/agents/test_auto_chain.py
"""Auto-chain multi-step planner: expand_chain, planner chain_until,
continuation queue, chain_note in coordinator confirms."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest

from backend.src.agents.write_registry import expand_chain
from backend.src.agents import write_gate


# ── expand_chain (total function) ────────────────────────────────────────────

def test_expand_one_step_sale():
    assert expand_chain("create_quotation", "confirm_sale_order") == \
        [("confirm_sale_order", "Xác nhận báo giá")]


def test_expand_full_sale_chain_to_invoice():
    steps = expand_chain("create_quotation", "post_invoice")
    assert [t for t, _ in steps] == ["confirm_sale_order", "deliver_order",
                                     "create_invoice_from_order", "post_invoice"]


def test_expand_purchase_chain():
    steps = expand_chain("create_rfq", "receive_order")
    assert [t for t, _ in steps] == ["confirm_purchase_order", "receive_order"]


def test_expand_edit_chain():
    assert expand_chain("update_quotation_lines", "confirm_sale_order") == \
        [("confirm_sale_order", "Xác nhận báo giá")]


def test_expand_missing_or_same_returns_none():
    assert expand_chain("create_quotation", None) is None
    assert expand_chain("create_quotation", "") is None
    assert expand_chain("create_quotation", "create_quotation") is None


def test_expand_unknown_or_unreachable_returns_none():
    assert expand_chain("create_quotation", "frobnicate") is None
    assert expand_chain("inventory_adjustment", "confirm_sale_order") is None
    # backwards: deliver comes AFTER confirm, can't walk back
    assert expand_chain("deliver_order", "confirm_sale_order") is None
    # cross-chain: sale start can't reach purchase tool
    assert expand_chain("create_quotation", "receive_order") is None


def test_expand_total_on_garbage():
    for a, b in [(None, None), (123, "x"), ("create_quotation", 123),
                 ({}, []), (None, "confirm_sale_order"),
                 ({}, "confirm_sale_order"), ([1, 2], "confirm_sale_order"),
                 (123, "confirm_sale_order")]:
        assert expand_chain(a, b) is None


# ── planner: chain_until → auto_chain + chain_note ───────────────────────────
import json
from unittest.mock import MagicMock
from langchain_core.messages import HumanMessage

from backend.src.agents.state import ERPAgentState
from backend.tests.conftest import make_mock_llm
import backend.src.agents.nodes as nodes_mod
from backend.src.agents.nodes import make_erp_write_planner_node


def _pstate(text):
    return ERPAgentState(messages=[HumanMessage(content=text)],
                         intent="erp_write", pending_action=None, confirmed=None)


def _mk_llm(payload):
    return make_mock_llm(json.dumps(payload, ensure_ascii=False))


@pytest.mark.asyncio
async def test_planner_valid_chain_sets_auto_chain_and_note(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    llm = _mk_llm({"tool": "create_quotation",
                   "args": {"partner_name": "Azur",
                            "lines": [{"product": "Tủ", "qty": 2}]},
                   "summary": "Tạo báo giá và xác nhận",
                   "chain_until": "confirm_sale_order"})
    out = await make_erp_write_planner_node(llm)(
        _pstate("tạo báo giá cho Azur, 2 Tủ rồi xác nhận luôn"))
    assert out["auto_chain"] == ["confirm_sale_order"]
    assert out["pending_action"]["chain_note"] == \
        "\n\nSau đó tự động: Xác nhận báo giá"


@pytest.mark.asyncio
async def test_planner_bogus_chain_falls_back_single_step(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    llm = _mk_llm({"tool": "create_quotation",
                   "args": {"partner_name": "Azur", "lines": []},
                   "summary": "Tạo báo giá", "chain_until": "frobnicate"})
    out = await make_erp_write_planner_node(llm)(_pstate("tạo báo giá"))
    assert out["auto_chain"] is None
    assert "chain_note" not in out["pending_action"]


@pytest.mark.asyncio
async def test_planner_noncoordinated_chain_note_in_confirm(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    captured = {}
    monkeypatch.setattr(nodes_mod, "_interrupt",
                        lambda p: captured.update(p) or True)
    llm = _mk_llm({"tool": "confirm_sale_order", "args": {"order_ref": "S00012"},
                   "summary": "Xác nhận S00012", "chain_until": "deliver_order"})
    out = await make_erp_write_planner_node(llm)(
        _pstate("xác nhận S00012 rồi giao hàng luôn"))
    assert "Sau đó tự động: Giao hàng" in captured["question"]
    assert out["auto_chain"] == ["deliver_order"]
    assert out["confirmed"] is True


@pytest.mark.asyncio
async def test_planner_gate_return_has_auto_chain_key(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: False)
    out = await make_erp_write_planner_node(MagicMock())(_pstate("x"))
    assert "auto_chain" in out and out["auto_chain"] is None


@pytest.mark.asyncio
async def test_planner_non_json_return_has_auto_chain_key(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    out = await make_erp_write_planner_node(make_mock_llm("not json"))(_pstate("x"))
    assert "auto_chain" in out and out["auto_chain"] is None


# ── continuation: queue consumption ──────────────────────────────────────────
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from backend.src.agents.continuation import make_write_continuation_node


def _cgraph():
    g = StateGraph(ERPAgentState)
    g.add_node("write_continuation", make_write_continuation_node())
    g.set_entry_point("write_continuation")
    g.add_edge("write_continuation", END)
    return g.compile(checkpointer=MemorySaver())


def _lw(tool="create_quotation", ok=True, ref="S00031", res_id=42):
    return {"tool": tool, "ok": ok, "ref": ref, "model": "sale.order",
            "res_id": res_id, "state": "draft",
            "display": "Đã tạo báo giá S00031 (nháp) cho Azure."}


def _cstate(lw, queue):
    return {"messages": [], "intent": "erp_write", "confirmed": True,
            "pending_action": {"tool": "x"}, "last_write": lw,
            "auto_chain": queue}


@pytest.mark.asyncio
async def test_auto_proceed_no_interrupt():
    res = await _cgraph().ainvoke(_cstate(_lw(), ["confirm_sale_order"]),
                                  {"configurable": {"thread_id": "a1"}})
    assert "__interrupt__" not in res
    assert res["pending_action"] == {"tool": "confirm_sale_order",
                                     "args": {"order_ref": "S00031"},
                                     "summary": "Xác nhận báo giá"}
    assert res["confirmed"] is True
    assert res["last_write"] is None
    assert res["auto_chain"] is None          # queue exhausted


@pytest.mark.asyncio
async def test_auto_proceed_keeps_rest_of_queue():
    res = await _cgraph().ainvoke(
        _cstate(_lw(), ["confirm_sale_order", "deliver_order"]),
        {"configurable": {"thread_id": "a2"}})
    assert "__interrupt__" not in res
    assert res["auto_chain"] == ["deliver_order"]


@pytest.mark.asyncio
async def test_head_mismatch_falls_back_to_menu():
    graph, cfg = _cgraph(), {"configurable": {"thread_id": "a3"}}
    res = await graph.ainvoke(_cstate(_lw(), ["deliver_order"]), cfg)
    itr = res["__interrupt__"][0].value       # menu, NOT auto-run of wrong step
    assert itr["kind"] == "next_action"
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert res["auto_chain"] is None


@pytest.mark.asyncio
async def test_failed_write_with_queue_warns_and_clears():
    res = await _cgraph().ainvoke(_cstate(_lw(ok=False), ["confirm_sale_order"]),
                                  {"configurable": {"thread_id": "a4"}})
    assert "__interrupt__" not in res
    content = res["messages"][-1].content
    assert "Đã tạo báo giá S00031" in content   # lw['display'] must survive
    assert "Chuỗi tự động dừng" in content
    assert res["auto_chain"] is None and res["last_write"] is None


@pytest.mark.asyncio
async def test_offchain_tool_with_queue_warns():
    # vd nhánh flag của edit: write chạy OK nhưng tool không có NEXT_STEPS entry
    lw = _lw(tool="flag_order_for_review", ok=True)
    res = await _cgraph().ainvoke(_cstate(lw, ["confirm_sale_order"]),
                                  {"configurable": {"thread_id": "a5"}})
    content = res["messages"][-1].content
    assert lw["display"] in content            # real flag-note result survives
    assert "Chuỗi tự động dừng" in content
    assert res["auto_chain"] is None


@pytest.mark.asyncio
async def test_cancel_with_queue_is_silent():
    # lw falsy = user đã hủy ở draft confirm — không write nào chạy → im lặng
    res = await _cgraph().ainvoke(_cstate(None, ["confirm_sale_order"]),
                                  {"configurable": {"thread_id": "a6"}})
    assert res["messages"] == []
    assert res["auto_chain"] is None


@pytest.mark.asyncio
async def test_every_branch_writes_auto_chain_key_direct_call():
    node = make_write_continuation_node()
    out = await node({"messages": [], "last_write": None, "auto_chain": None})
    assert "auto_chain" in out and out["auto_chain"] is None


# ── chain_note in coordinator confirms ───────────────────────────────────────
from unittest.mock import MagicMock
import backend.src.agents.create_order as co
import backend.src.agents.edit_order as eo
from backend.src.agents.create_order import render_draft
from backend.src.agents.edit_order import _render_diff

NOTE = "\n\nSau đó tự động: Xác nhận báo giá"


def test_render_draft_note_before_question():
    out = render_draft({"name": "Azur"},
                       [{"name": "Tủ", "qty": 2, "unit_price": 5.0, "subtotal": 10.0}],
                       10.0, note=NOTE)
    assert "Sau đó tự động: Xác nhận báo giá" in out
    assert out.index("Sau đó tự động") < out.index("Xác nhận? (có / không)")


def test_render_draft_purchase_variant_note():
    out = render_draft({"name": "ACME"}, [{"name": "Tủ", "qty": 2}], None,
                       head="Đơn mua từ", note=NOTE)
    assert out.index("Sau đó tự động") < out.index("Xác nhận? (có / không)")


def test_render_draft_no_note_unchanged():
    out = render_draft({"name": "Azur"},
                       [{"name": "Tủ", "qty": 2, "unit_price": 5.0, "subtotal": 10.0}],
                       10.0)
    assert "Sau đó tự động" not in out


def test_render_diff_note_before_question():
    out = _render_diff(eo.SALE_EDIT_CFG, "S00040", "Azur",
                       ["Tủ × 2 = 10"], [], [], NOTE)
    assert out.index("Sau đó tự động") < out.index("Xác nhận? (có / không)")


def _ok_env(matches, needs=False):
    return {"status": "success", "display": "x",
            "data": {"matches": matches, "needs_disambiguation": needs}}


@pytest.mark.asyncio
async def test_create_order_confirm_shows_chain_note(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(co.sales, "find_customer",
                        lambda *a, **k: _ok_env([{"id": 41, "name": "Azur", "score": 1}]))
    monkeypatch.setattr(co.inventory, "find_product",
                        lambda *a, **k: _ok_env([{"id": 552, "name": "Tủ", "score": 1}]))
    monkeypatch.setattr(co.sales, "get_product_price",
                        lambda *a, **k: {"status": "success",
                                         "data": {"price": 100000.0}, "display": "x"})
    g = StateGraph(ERPAgentState)
    g.add_node("create_order", co.make_create_order_node(MagicMock(), []))
    g.set_entry_point("create_order")
    g.add_edge("create_order", END)
    graph = g.compile(checkpointer=MemorySaver())
    state = {"messages": [], "intent": "erp_write", "confirmed": None,
             "pending_action": {"tool": "create_quotation",
                                "args": {"partner_name": "Azur",
                                         "lines": [{"product": "Tủ", "qty": 2}]},
                                "chain_note": NOTE}}
    res = await graph.ainvoke(state, {"configurable": {"thread_id": "n1"}})
    q = res["__interrupt__"][0].value["question"]
    assert "Sau đó tự động: Xác nhận báo giá" in q
    assert q.index("Sau đó tự động") < q.index("Xác nhận? (có / không)")


# ── integration: create → (auto) confirm qua graph write thật ────────────────
from backend.src.agents.nodes import make_erp_write_executor_node
from backend.src.agents.graph import (_route_after_write_planner,
                                      _route_after_continuation)
from backend.src.agents.write_registry import WRITE_COORDINATORS


def _env_tool(name, ref, state_val, display, rec):
    t = MagicMock()
    t.name = name

    async def ainvoke(args):
        rec.append((name, args))
        return json.dumps({"ok": True, "ref": ref, "model": "sale.order",
                           "res_id": 99, "state": state_val, "display": display},
                          ensure_ascii=False)

    t.ainvoke = ainvoke
    return t


def _write_graph(llm, tools):
    g = StateGraph(ERPAgentState)
    g.add_node("erp_write_planner", make_erp_write_planner_node(llm))
    g.add_node("erp_write_executor", make_erp_write_executor_node(tools))
    for spec in WRITE_COORDINATORS.values():
        g.add_node(spec.node, spec.build(llm, tools))
    g.add_node("write_continuation", make_write_continuation_node())
    g.set_entry_point("erp_write_planner")
    targets = {END: END, "erp_write_executor": "erp_write_executor"}
    targets.update({s.node: s.node for s in WRITE_COORDINATORS.values()})
    g.add_conditional_edges("erp_write_planner", _route_after_write_planner, targets)
    g.add_edge("erp_write_executor", "write_continuation")
    for s in WRITE_COORDINATORS.values():
        g.add_edge(s.node, "write_continuation")
    g.add_conditional_edges("write_continuation", _route_after_continuation,
                            {"erp_write_executor": "erp_write_executor", END: END})
    return g.compile(checkpointer=MemorySaver())


@pytest.mark.asyncio
async def test_two_step_chain_one_confirm_end_to_end(monkeypatch):
    monkeypatch.setattr(write_gate, "write_actions_enabled", lambda: True)
    monkeypatch.setattr(co.sales, "find_customer",
                        lambda *a, **k: _ok_env([{"id": 41, "name": "Azur", "score": 1}]))
    monkeypatch.setattr(co.inventory, "find_product",
                        lambda *a, **k: _ok_env([{"id": 552, "name": "Tủ", "score": 1}]))
    monkeypatch.setattr(co.sales, "get_product_price",
                        lambda *a, **k: {"status": "success",
                                         "data": {"price": 100000.0}, "display": "x"})
    rec = []
    tools = [
        _env_tool("create_quotation", "S00099", "draft",
                  "Đã tạo báo giá S00099 (nháp) cho Azur (1 dòng).", rec),
        _env_tool("confirm_sale_order", "S00099", "sale",
                  "Đã xác nhận đơn S00099.", rec),
    ]
    llm = _mk_llm({"tool": "create_quotation",
                   "args": {"partner_name": "Azur",
                            "lines": [{"product": "Tủ", "qty": 2}]},
                   "summary": "Tạo báo giá và xác nhận",
                   "chain_until": "confirm_sale_order"})
    graph = _write_graph(llm, tools)
    cfg = {"configurable": {"thread_id": "e2e-1"}}

    # Turn 1: draft confirm hiện chain_note — interrupt DUY NHẤT trước khi chạy
    res = await graph.ainvoke(
        {"messages": [HumanMessage(content="tạo báo giá cho Azur, 2 Tủ rồi xác nhận luôn")],
         "intent": "erp_write", "pending_action": None, "confirmed": None}, cfg)
    q = res["__interrupt__"][0].value["question"]
    assert "Sau đó tự động: Xác nhận báo giá" in q
    assert rec == []                                  # chưa write gì

    # Resume "có": create chạy → continuation auto-proceed (KHÔNG interrupt)
    # → executor confirm chạy → continuation: queue hết → menu bước kế (Giao hàng)
    res = await graph.ainvoke(Command(resume=True), cfg)
    assert [n for n, _ in rec] == ["create_quotation", "confirm_sale_order"]
    assert rec[1][1] == {"order_ref": "S00099"}       # args từ registry lambda
    itr = res["__interrupt__"][0].value
    assert itr["kind"] == "next_action"
    assert "Giao hàng" in itr["question"]             # menu chỉ hiện SAU chuỗi

    # Dừng tại menu
    res = await graph.ainvoke(Command(resume=False), cfg)
    assert res["messages"][-1].content == "Đã dừng tại đây."
    assert res["auto_chain"] is None


def test_expand_full_sale_chain_to_register_payment():
    steps = expand_chain("create_quotation", "register_payment")
    assert [t for t, _ in steps] == ["confirm_sale_order", "deliver_order",
                                     "create_invoice_from_order", "post_invoice",
                                     "register_payment"]


def test_post_invoice_next_step_is_register_payment():
    from backend.src.agents.write_registry import NEXT_STEPS
    step = NEXT_STEPS["post_invoice"]
    assert step.tool == "register_payment"
    assert step.args({"res_id": 64}) == {"invoice_id": 64}


def test_expand_create_lead_chain():
    steps = expand_chain("create_lead", "convert_lead")
    assert steps == [("convert_lead", "Chuyển thành cơ hội")]


def test_create_lead_next_step_args_lambda():
    from backend.src.agents.write_registry import NEXT_STEPS
    step = NEXT_STEPS["create_lead"]
    assert step.tool == "convert_lead"
    assert step.args({"res_id": 45}) == {"lead_id": 45}
