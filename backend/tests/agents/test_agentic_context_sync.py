import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import json
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.src.agents.agentic_context_sync import make_agentic_context_sync_node


def _env(ref="P00021", model="purchase.order", ok=True, display="Đã nhận hàng cho đơn mua P00021."):
    return json.dumps({"ok": ok, "ref": ref, "model": model, "res_id": 9,
                       "state": "purchase", "display": display}, ensure_ascii=False)


@pytest.mark.asyncio
async def test_sets_working_context_from_write_envelope():
    node = make_agentic_context_sync_node()
    state = {"messages": [
        HumanMessage(content="nhập kho P00021"),
        AIMessage(content=""),
        ToolMessage(content=_env(), tool_call_id="c1"),
        AIMessage(content="Đã xong."),
    ]}
    upd = await node(state)
    assert upd == {"working_context": {
        "ref": "P00021", "model": "purchase.order",
        "display": "Đã nhận hàng cho đơn mua P00021."}}


@pytest.mark.asyncio
async def test_refusal_string_tool_message_yields_no_update():
    # REFUSED_MSG không phải JSON → json.loads fail → bỏ qua, không update.
    node = make_agentic_context_sync_node()
    state = {"messages": [
        HumanMessage(content="nhập kho P00021"),
        ToolMessage(content="Người dùng TỪ CHỐI xác nhận — KHÔNG thực hiện "
                            "thao tác. Hãy hỏi người dùng muốn làm gì tiếp.",
                    tool_call_id="c1"),
        AIMessage(content="Đã hủy."),
    ]}
    assert await node(state) == {}


@pytest.mark.asyncio
async def test_read_tool_result_yields_no_update():
    # Kết quả erp_query read có shape {"status": ...}, KHÔNG có key "ok" →
    # derive_working_context trả None → không update. Read không set context
    # (parity với tầng 1: chỉ write mới set).
    node = make_agentic_context_sync_node()
    read_result = json.dumps({"status": "success",
                              "data": {"order": {"id": 9, "name": "P00021"}},
                              "display": "x"}, ensure_ascii=False)
    state = {"messages": [
        HumanMessage(content="nhập kho P00021"),
        ToolMessage(content=read_result, tool_call_id="c1"),
        AIMessage(content="..."),
    ]}
    assert await node(state) == {}


@pytest.mark.asyncio
async def test_most_recent_envelope_wins():
    node = make_agentic_context_sync_node()
    state = {"messages": [
        HumanMessage(content="..."),
        ToolMessage(content=_env(ref="S00012", model="sale.order",
                                 display="Đã giao hàng."), tool_call_id="c1"),
        ToolMessage(content=_env(ref="P00021", model="purchase.order",
                                 display="Đã nhận hàng."), tool_call_id="c2"),
        AIMessage(content="Xong."),
    ]}
    upd = await node(state)
    assert upd["working_context"]["ref"] == "P00021"


@pytest.mark.asyncio
async def test_scan_stops_at_current_turn_boundary():
    # ToolMessage nằm TRƯỚC HumanMessage cuối (lượt trước) không được đọc —
    # quét ngược dừng ngay khi gặp human.
    node = make_agentic_context_sync_node()
    state = {"messages": [
        HumanMessage(content="lượt cũ"),
        ToolMessage(content=_env(), tool_call_id="c1"),
        HumanMessage(content="lượt mới"),
        AIMessage(content="trả lời lượt mới, không tool nào chạy"),
    ]}
    assert await node(state) == {}


@pytest.mark.asyncio
async def test_non_order_model_envelope_yields_no_update():
    # model ngoài ORDER_MODELS (vd account.move) bị derive_working_context
    # loại — giữ nguyên quyết định không nhớ hóa đơn.
    node = make_agentic_context_sync_node()
    state = {"messages": [
        HumanMessage(content="..."),
        ToolMessage(content=_env(ref="HD001", model="account.move"),
                    tool_call_id="c1"),
        AIMessage(content="..."),
    ]}
    assert await node(state) == {}


@pytest.mark.asyncio
async def test_empty_or_missing_messages_never_raises():
    node = make_agentic_context_sync_node()
    assert await node({"messages": []}) == {}
    assert await node({}) == {}
