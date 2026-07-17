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
            {"name": "receive_order", "args": {"order_ref": "P00003"}, "id": "c4"}]),
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
    payload = res["__interrupt__"][0].value  # cổng xác nhận code-enforced
    assert payload["kind"] == "confirm"
    assert "P00003" in payload["question"]

    res = await graph.ainvoke(Command(resume=True), cfg)
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
             "args": {"order_ref": "P00003",
                     "note": "Thiếu hàng: nhận 7, PO 10."}, "id": "c3"}]),
        AIMessage(content="Đã ghi nhận thiếu hàng, chờ xử lý."),
    ]
    node = sawr.make_node(_SeqModel(steps), _MCP_TOOLS)
    graph = _graph(node)
    cfg = _cfg("t2")

    await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00003")]}, cfg)
    res = await graph.ainvoke(Command(resume="7"), cfg)
    assert res["__interrupt__"][0].value["kind"] == "confirm"  # cổng ghi chú

    res = await graph.ainvoke(Command(resume=True), cfg)
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
             "args": {"order_ref": "P00003",
                     "note": "Thừa hàng: nhận 15, PO 10."}, "id": "c3"}]),
        AIMessage(content="Đã ghi nhận nhận thừa."),
    ]
    node = sawr.make_node(_SeqModel(steps), _MCP_TOOLS)
    graph = _graph(node)
    cfg = _cfg("t3")

    await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00003")]}, cfg)
    res = await graph.ainvoke(Command(resume="15"), cfg)
    assert res["__interrupt__"][0].value["kind"] == "confirm"  # cổng ghi chú

    res = await graph.ainvoke(Command(resume=True), cfg)
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


def test_make_node_does_not_crash_with_empty_mcp_tools():
    # Regression guard: _build_tools previously did unguarded by_name["receive_order"]
    # indexing, which crashed build_graph(tools=[]) — an established test convention
    # used throughout test_graph_build.py — at graph-CONSTRUCTION time, not request
    # time. Any MCP tool-name gap would have taken down the entire app's graph, not
    # just this one skill. Must build cleanly with fewer tools instead.
    node = sawr.make_node(_SeqModel([AIMessage(content="ok")]), [])
    assert node is not None


def _recording_tools():
    """2 fake tool ghi MCP có ghi nhận lệnh gọi — để assert wrapper chỉ gọi
    tool thật SAU resume=True, đúng 1 lần, đúng args."""
    calls = {"receive": [], "flag": []}

    @tool("receive_order")
    def rec_receive(order_ref: str) -> str:
        """Fake receive_order MCP tool (recording)."""
        calls["receive"].append({"order_ref": order_ref})
        return json.dumps({"ok": True,
                           "display": f"Đã nhận hàng cho đơn mua {order_ref}."},
                          ensure_ascii=False)

    @tool("flag_order_for_review")
    def rec_flag(model: str, order_ref: str, note: str) -> str:
        """Fake flag_order_for_review MCP tool (recording)."""
        calls["flag"].append({"model": model, "order_ref": order_ref, "note": note})
        return json.dumps({"ok": True,
                           "display": f"Đã ghi chú nội bộ trên đơn {order_ref}."},
                          ensure_ascii=False)

    return [rec_receive, rec_flag], calls


@pytest.mark.asyncio
async def test_confirm_gate_parks_before_write_then_writes_once_on_yes():
    tools, calls = _recording_tools()
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "receive_order", "args": {"order_ref": "P00021"}, "id": "g1"}]),
        AIMessage(content="Đã nhận hàng xong."),
    ]
    graph = _graph(sawr.make_node(_SeqModel(steps), tools))
    cfg = _cfg("g1")

    res = await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00021")]}, cfg)
    payload = res["__interrupt__"][0].value
    assert payload["kind"] == "confirm"
    assert "P00021" in payload["question"]
    assert "expires_at" in payload
    assert calls["receive"] == []  # bất biến: KHÔNG ghi trước khi có YES

    res = await graph.ainvoke(Command(resume=True), cfg)
    assert "__interrupt__" not in res
    assert calls["receive"] == [{"order_ref": "P00021"}]  # đúng 1 lần, đúng args
    tool_texts = [m.content for m in res["messages"] if m.type == "tool"]
    assert any("Đã nhận hàng" in t for t in tool_texts)


@pytest.mark.asyncio
async def test_confirm_gate_refusal_blocks_write():
    tools, calls = _recording_tools()
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "receive_order", "args": {"order_ref": "P00021"}, "id": "g2"}]),
        AIMessage(content="Đã hủy theo yêu cầu."),
    ]
    graph = _graph(sawr.make_node(_SeqModel(steps), tools))
    cfg = _cfg("g2")

    await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00021")]}, cfg)
    res = await graph.ainvoke(Command(resume=False), cfg)

    assert "__interrupt__" not in res
    assert calls["receive"] == []  # từ chối → 0 write
    tool_texts = [m.content for m in res["messages"] if m.type == "tool"]
    assert any(sawr.REFUSED_MSG in t for t in tool_texts)
    assert res["messages"][-1].type == "ai"  # model vẫn chốt được câu trả lời


@pytest.mark.asyncio
async def test_flag_wrapper_hardcodes_model_param():
    # Probe D (spec §1): qwythos-9b từng truyền model="P00003" thay vì
    # "purchase.order". Wrapper thu hẹp schema loại lỗi này bằng cấu trúc:
    # model không truyền được tham số `model`, wrapper tự điền giá trị đúng.
    tools, calls = _recording_tools()
    steps = [
        AIMessage(content="", tool_calls=[
            {"name": "flag_order_for_review",
             "args": {"order_ref": "P00021",
                      "note": "Thiếu hàng: nhận 7, PO 10."}, "id": "g3"}]),
        AIMessage(content="Đã ghi chú."),
    ]
    graph = _graph(sawr.make_node(_SeqModel(steps), tools))
    cfg = _cfg("g3")

    res = await graph.ainvoke({"messages": [HumanMessage(content="nhập kho P00021")]}, cfg)
    payload = res["__interrupt__"][0].value
    assert payload["kind"] == "confirm"
    assert "Thiếu hàng" in payload["question"]  # câu hỏi hiển thị note thật

    await graph.ainvoke(Command(resume=True), cfg)
    assert calls["flag"] == [{"model": "purchase.order", "order_ref": "P00021",
                              "note": "Thiếu hàng: nhận 7, PO 10."}]


def test_no_raw_write_tools_exposed_to_model():
    # Bất biến cốt lõi (spec §4.1): model không bao giờ thấy tool MCP ghi thô
    # — mọi đường ghi đều xuyên qua wrapper có cổng xác nhận. So sánh identity
    # để chắc chắn object thô không lọt vào danh sách, kể cả khi trùng tên.
    tools, _ = _recording_tools()
    built = sawr._build_tools(tools)
    raw_ids = {id(t) for t in tools}
    assert all(id(t) not in raw_ids for t in built)
    names = {t.name for t in built}
    assert {"receive_order", "flag_order_for_review"} <= names  # wrapper thế chỗ đủ


def test_build_tools_empty_mcp_exposes_no_write_wrappers():
    # Degradation (spec §4.2, kế thừa eb61ade): thiếu tool MCP → không dựng
    # wrapper tương ứng, chỉ còn đúng 2 tool an toàn.
    names = {t.name for t in sawr._build_tools([])}
    assert names == {"ask_human", "get_purchase_order_detail"}


def test_sop_prompt_contains_no_po_bridge_message_verbatim():
    # Guards against a broken f-string interpolation silently dropping the
    # bridge text from the prompt the model actually sees.
    assert sawr.NO_PO_BRIDGE_MSG in sawr.SOP_PROMPT
