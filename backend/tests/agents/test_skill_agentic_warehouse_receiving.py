import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from typing import TypedDict, Annotated
from pydantic import PrivateAttr
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command
import backend.src.agents.skill_agentic_warehouse_receiving as sawr
import backend.src.erp_query.purchase as erp_purchase


class _SeqModel(BaseChatModel):
    """Plays back a fixed list of AIMessage responses in order, one per
    model invocation. LangGraph does not replay already-completed steps of
    create_agent's own internal graph on resume (only the interrupted step
    itself replays its cached value) — verified empirically before this
    plan was written — so a plain index counter is safe here; it will not
    be advanced more than once per genuinely new model call."""
    _responses: list = PrivateAttr()
    _idx: list = PrivateAttr(default_factory=lambda: [0])

    def __init__(self, responses, **kwargs):
        super().__init__(**kwargs)
        self._responses = list(responses)

    @property
    def _llm_type(self) -> str:
        return "seq"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        i = self._idx[0]
        self._idx[0] += 1
        return ChatResult(generations=[ChatGeneration(message=self._responses[i])])


@tool("receive_order")
def _fake_receive_order(order_ref: str) -> str:
    """Fake receive_order MCP tool."""
    # ensure_ascii=False: matches the real MCP envelope() helper
    # (mcp-servers/odoo/helpers.py) so the Vietnamese display text survives
    # as literal characters in the ToolMessage content, not \uXXXX escapes
    # (the brief's fixture omitted this, which made the "Đã nhận hàng"
    # substring assertion below unsatisfiable regardless of implementation
    # correctness — see task-2-report.md for details).
    return json.dumps({"ok": True, "ref": order_ref, "model": "purchase.order",
                       "res_id": 9, "state": "purchase",
                       "display": f"Đã nhận hàng cho đơn mua {order_ref}."},
                      ensure_ascii=False)


@tool("flag_order_for_review")
def _fake_flag_order(model: str, order_ref: str, note: str) -> str:
    """Fake flag_order_for_review MCP tool."""
    return json.dumps({"ok": True, "ref": order_ref, "model": model,
                       "res_id": 9, "state": "purchase",
                       "display": f"Đã ghi chú nội bộ trên đơn {order_ref}."},
                      ensure_ascii=False)


_MCP_TOOLS = [_fake_receive_order, _fake_flag_order]


def _po_detail(qty=10.0):
    return {"status": "success",
            "data": {"order": {"id": 9, "name": "P00003"},
                     "lines": [{"product_id": [1, "Tủ"], "product_qty": qty}]},
            "display": "x"}


def _graph(node):
    class OuterState(TypedDict):
        messages: Annotated[list, add_messages]
    g = StateGraph(OuterState)
    g.add_node("skill_agent", node)
    g.set_entry_point("skill_agent")
    g.add_edge("skill_agent", END)
    return g.compile(checkpointer=MemorySaver())


def _cfg(thread_id):
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 15}


@pytest.mark.asyncio
async def test_match_scenario_receives(monkeypatch):
    monkeypatch.setattr(erp_purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qty=10.0))
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "ask_human", "args": {"question": "Số lượng thực nhận?"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[
            {"name": "get_purchase_order_detail", "args": {"ref": "P00003"}, "id": "c2"}]),
        AIMessage(content="", tool_calls=[
            {"name": "ask_human", "args": {"question": "QC kết quả?"}, "id": "c3"}]),
        AIMessage(content="", tool_calls=[
            {"name": "ask_human", "args": {"question": "Xác nhận nhận hàng?"}, "id": "c4"}]),
        AIMessage(content="", tool_calls=[
            {"name": "receive_order", "args": {"order_ref": "P00003"}, "id": "c5"}]),
        AIMessage(content="Đã nhận hàng xong."),
    ]
    node = sawr.make_node(_SeqModel(steps), _MCP_TOOLS)
    graph = _graph(node)
    cfg = _cfg("t1")

    res = await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00003")]}, cfg)
    assert res["__interrupt__"][0].value["kind"] == "free_text"

    res = await graph.ainvoke(Command(resume="10"), cfg)
    assert res["__interrupt__"][0].value["kind"] == "free_text"
    assert "QC" in res["__interrupt__"][0].value["question"]

    res = await graph.ainvoke(Command(resume="đạt"), cfg)
    assert "__interrupt__" in res  # final confirm-before-write checkpoint

    res = await graph.ainvoke(Command(resume="có"), cfg)
    assert "__interrupt__" not in res
    tool_texts = [m.content for m in res["messages"] if m.type == "tool"]
    assert any("Đã nhận hàng" in t for t in tool_texts)


@pytest.mark.asyncio
async def test_short_scenario_flags_not_receives(monkeypatch):
    monkeypatch.setattr(erp_purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qty=10.0))
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "ask_human", "args": {"question": "Số lượng thực nhận?"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[
            {"name": "get_purchase_order_detail", "args": {"ref": "P00003"}, "id": "c2"}]),
        AIMessage(content="", tool_calls=[
            {"name": "flag_order_for_review",
             "args": {"model": "purchase.order", "order_ref": "P00003",
                     "note": "Thiếu hàng: nhận 7, PO 10."}, "id": "c3"}]),
        AIMessage(content="Đã ghi nhận thiếu hàng, chờ xử lý."),
    ]
    node = sawr.make_node(_SeqModel(steps), _MCP_TOOLS)
    graph = _graph(node)
    cfg = _cfg("t2")

    await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00003")]}, cfg)
    res = await graph.ainvoke(Command(resume="7"), cfg)

    assert "__interrupt__" not in res
    tool_names_called = [tc["name"] for m in res["messages"] if m.type == "ai"
                         for tc in (m.tool_calls or [])]
    assert "flag_order_for_review" in tool_names_called
    assert "receive_order" not in tool_names_called


@pytest.mark.asyncio
async def test_excess_scenario_flags_not_receives(monkeypatch):
    monkeypatch.setattr(erp_purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qty=10.0))
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "ask_human", "args": {"question": "Số lượng thực nhận?"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[
            {"name": "get_purchase_order_detail", "args": {"ref": "P00003"}, "id": "c2"}]),
        AIMessage(content="", tool_calls=[
            {"name": "flag_order_for_review",
             "args": {"model": "purchase.order", "order_ref": "P00003",
                     "note": "Thừa hàng: nhận 15, PO 10."}, "id": "c3"}]),
        AIMessage(content="Đã ghi nhận nhận thừa."),
    ]
    node = sawr.make_node(_SeqModel(steps), _MCP_TOOLS)
    graph = _graph(node)
    cfg = _cfg("t3")

    await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00003")]}, cfg)
    res = await graph.ainvoke(Command(resume="15"), cfg)

    assert "__interrupt__" not in res
    tool_names_called = [tc["name"] for m in res["messages"] if m.type == "ai"
                         for tc in (m.tool_calls or [])]
    assert "flag_order_for_review" in tool_names_called
    assert "receive_order" not in tool_names_called


@pytest.mark.asyncio
async def test_qc_fail_scenario_does_not_receive(monkeypatch):
    monkeypatch.setattr(erp_purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qty=10.0))
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "ask_human", "args": {"question": "Số lượng thực nhận?"}, "id": "c1"}]),
        AIMessage(content="", tool_calls=[
            {"name": "get_purchase_order_detail", "args": {"ref": "P00003"}, "id": "c2"}]),
        AIMessage(content="", tool_calls=[
            {"name": "ask_human", "args": {"question": "QC kết quả?"}, "id": "c3"}]),
        AIMessage(content="QC không đạt, không nhận hàng, chờ xử lý theo quy trình trả hàng."),
    ]
    node = sawr.make_node(_SeqModel(steps), _MCP_TOOLS)
    graph = _graph(node)
    cfg = _cfg("t4")

    await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00003")]}, cfg)
    await graph.ainvoke(Command(resume="10"), cfg)
    res = await graph.ainvoke(Command(resume="không đạt"), cfg)

    assert "__interrupt__" not in res
    tool_names_called = [tc["name"] for m in res["messages"] if m.type == "ai"
                         for tc in (m.tool_calls or [])]
    assert "receive_order" not in tool_names_called
    assert "flag_order_for_review" not in tool_names_called
    assert "không đạt" in res["messages"][-1].content


@pytest.mark.asyncio
async def test_recursion_limit_bounds_a_looping_agent(monkeypatch):
    call_count = {"n": 0}

    class _LoopingModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "looping"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            call_count["n"] += 1
            msg = AIMessage(content="", tool_calls=[
                {"name": "get_purchase_order_detail", "args": {"ref": "P00003"},
                 "id": f"loop{call_count['n']}"}])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    monkeypatch.setattr(erp_purchase, "get_purchase_order_detail",
                        lambda *a, **k: _po_detail(qty=10.0))
    node = sawr.make_node(_LoopingModel(), _MCP_TOOLS)
    graph = _graph(node)
    cfg = {"configurable": {"thread_id": "t5"}, "recursion_limit": 6}

    with pytest.raises(GraphRecursionError):
        await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00003")]}, cfg)
    assert call_count["n"] < 20  # bounded well below the ~25 LangGraph default
