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
import backend.src.agents.skill_agentic_delivery as sad
import backend.src.erp_query.sales as erp_sales


class _SeqModel(BaseChatModel):
    """Plays back a fixed list of AIMessage responses in order, one per
    model invocation. Pattern verified in
    test_skill_agentic_warehouse_receiving.py — LangGraph does not replay
    already-completed steps of create_agent's own internal graph on resume
    (only the interrupted step itself replays its cached value), so a plain
    index counter is safe here."""
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


def _recording_tools(deliver_result=None):
    """1 fake tool ghi MCP có ghi nhận lệnh gọi — để assert wrapper chỉ gọi
    tool thật SAU resume=True, đúng 1 lần, đúng args. deliver_result: chuỗi
    JSON tùy chỉnh trả về (mặc định = kịch bản 'đã giao hàng' thành công)."""
    calls = {"deliver": []}
    result = deliver_result or json.dumps(
        {"ok": True, "ref": "S00012", "model": "sale.order", "res_id": 12,
         "state": "sale", "display": "Đã giao hàng cho đơn S00012 (1 phiếu)."},
        ensure_ascii=False)

    @tool("deliver_order")
    def rec_deliver(order_ref: str) -> str:
        """Fake deliver_order MCP tool (recording)."""
        calls["deliver"].append({"order_ref": order_ref})
        return result

    return [rec_deliver], calls


def _po_detail_fn(ref, *, gw=None):
    return {"status": "success",
            "data": {"order": {"id": 12, "name": ref, "state": "sale"},
                     "lines": [{"product_id": [1, "Tủ gỗ"], "product_uom_qty": 10.0,
                               "price_unit": 100.0, "price_subtotal": 1000.0}]},
            "display": "x"}


def _graph(node):
    class OuterState(TypedDict):
        messages: Annotated[list, add_messages]
    g = StateGraph(OuterState)
    g.add_node("skill_agent", node)
    g.set_entry_point("skill_agent")
    g.add_edge("skill_agent", END)
    return g.compile(checkpointer=MemorySaver())


def _cfg(thread_id, recursion_limit=15):
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": recursion_limit}


@pytest.mark.asyncio
async def test_confirm_gate_parks_before_write_then_writes_once_on_yes(monkeypatch):
    monkeypatch.setattr(erp_sales, "get_sale_order_detail", _po_detail_fn)
    tools, calls = _recording_tools()
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "get_sale_order_detail", "args": {"ref": "S00012"}, "id": "d1"}]),
        AIMessage(content="", tool_calls=[
            {"name": "deliver_order", "args": {"order_ref": "S00012"}, "id": "d2"}]),
        AIMessage(content="Đã giao hàng xong."),
    ]
    graph = _graph(sad.make_node(_SeqModel(steps), tools))
    cfg = _cfg("t1")

    res = await graph.ainvoke({"messages": [HumanMessage(content="giao hàng cho đơn bán S00012")]}, cfg)
    payload = res["__interrupt__"][0].value
    assert payload["kind"] == "confirm"
    assert "S00012" in payload["question"]
    assert "expires_at" in payload
    assert calls["deliver"] == []  # bất biến: KHÔNG ghi trước khi có YES

    res = await graph.ainvoke(Command(resume=True), cfg)
    assert "__interrupt__" not in res
    assert calls["deliver"] == [{"order_ref": "S00012"}]  # đúng 1 lần, đúng args
    tool_texts = [m.content for m in res["messages"] if m.type == "tool"]
    assert any("Đã giao hàng" in t for t in tool_texts)


@pytest.mark.asyncio
async def test_confirm_gate_refusal_blocks_write(monkeypatch):
    monkeypatch.setattr(erp_sales, "get_sale_order_detail", _po_detail_fn)
    tools, calls = _recording_tools()
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "get_sale_order_detail", "args": {"ref": "S00012"}, "id": "d1"}]),
        AIMessage(content="", tool_calls=[
            {"name": "deliver_order", "args": {"order_ref": "S00012"}, "id": "d2"}]),
        AIMessage(content="Đã hủy theo yêu cầu."),
    ]
    graph = _graph(sad.make_node(_SeqModel(steps), tools))
    cfg = _cfg("t2")

    await graph.ainvoke({"messages": [HumanMessage(content="giao hàng cho đơn bán S00012")]}, cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)

    assert "__interrupt__" not in res
    assert calls["deliver"] == []  # từ chối → 0 write
    tool_texts = [m.content for m in res["messages"] if m.type == "tool"]
    assert any(sad.REFUSED_MSG in t for t in tool_texts)
    assert res["messages"][-1].type == "ai"  # model vẫn chốt được câu trả lời


def test_no_raw_write_tool_exposed_to_model():
    # Bất biến cốt lõi (spec §4.4): model không bao giờ thấy tool MCP ghi
    # thô — mọi đường ghi đều xuyên qua wrapper có cổng xác nhận. So sánh
    # identity để chắc chắn object thô không lọt vào danh sách, kể cả khi
    # trùng tên.
    tools, _ = _recording_tools()
    built = sad._build_tools(tools)
    raw_ids = {id(t) for t in tools}
    assert all(id(t) not in raw_ids for t in built)
    names = {t.name for t in built}
    assert "deliver_order" in names  # wrapper thế chỗ đủ


def test_build_tools_empty_mcp_exposes_no_write_wrapper():
    # Degradation (kế thừa pattern eb61ade từ skill anh em): thiếu tool MCP
    # → không dựng wrapper, chỉ còn 2 tool an toàn.
    names = {t.name for t in sad._build_tools([])}
    assert names == {"ask_human", "get_sale_order_detail"}


@pytest.mark.asyncio
async def test_recursion_limit_bounds_a_looping_agent(monkeypatch):
    monkeypatch.setattr(erp_sales, "get_sale_order_detail", _po_detail_fn)
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
                {"name": "get_sale_order_detail", "args": {"ref": "S00012"},
                 "id": f"loop{call_count['n']}"}])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    tools, _ = _recording_tools()
    node = sad.make_node(_LoopingModel(), tools)
    graph = _graph(node)
    cfg = _cfg("t3", recursion_limit=6)

    with pytest.raises(GraphRecursionError):
        await graph.ainvoke({"messages": [HumanMessage(content="giao hàng cho đơn bán S00012")]}, cfg)
    assert call_count["n"] < 20  # bounded well below the ~25 LangGraph default


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome_display", [
    "Đơn S00012 không có phiếu cần giao (dịch vụ hoặc đã giao đủ).",
    "Phiếu giao của đơn S00012 chưa reserve đủ hàng (trạng thái: confirmed). Kiểm tra tồn kho trước khi giao.",
    "Phiếu WH/OUT/00001 cần thao tác bổ sung trên Odoo (wizard không hỗ trợ qua API). Vui lòng xử lý trực tiếp.",
    "Đã giao hàng cho đơn S00012 (1 phiếu).",
])
async def test_relays_all_four_deliver_order_outcomes_verbatim(monkeypatch, outcome_display):
    # deliver_order thật tự xử lý đủ 4 tình huống (server.py:380-426) — skill
    # chỉ cần relay nguyên văn kết quả tool trả về cho model, không tự diễn
    # giải (SOP_PROMPT bước 4). Test này khóa hành vi relay ở tầng tool-call:
    # bất kể fake tool trả outcome nào trong 4 outcome thật, wrapper vẫn trả
    # đúng nguyên văn đó lên tool-message sau resume=True.
    monkeypatch.setattr(erp_sales, "get_sale_order_detail", _po_detail_fn)
    fake_result = json.dumps({"ok": True, "ref": "S00012", "display": outcome_display},
                             ensure_ascii=False)
    tools, calls = _recording_tools(deliver_result=fake_result)
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "get_sale_order_detail", "args": {"ref": "S00012"}, "id": "d1"}]),
        AIMessage(content="", tool_calls=[
            {"name": "deliver_order", "args": {"order_ref": "S00012"}, "id": "d2"}]),
        AIMessage(content=outcome_display),
    ]
    graph = _graph(sad.make_node(_SeqModel(steps), tools))
    cfg = _cfg(f"t4-{hash(outcome_display)}")

    await graph.ainvoke({"messages": [HumanMessage(content="giao hàng cho đơn bán S00012")]}, cfg)
    res = await graph.ainvoke(Command(resume=True), cfg)

    assert "__interrupt__" not in res
    tool_texts = [m.content for m in res["messages"] if m.type == "tool"]
    assert any(outcome_display in t for t in tool_texts)


def test_make_node_does_not_crash_with_empty_mcp_tools():
    # Regression guard (kế thừa pattern eb61ade từ skill anh em): make_node
    # chạy EAGERLY tại thời điểm graph-construction, không phải request
    # time — phải build sạch kể cả thiếu tool MCP.
    node = sad.make_node(_SeqModel([AIMessage(content="ok")]), [])
    assert node is not None
