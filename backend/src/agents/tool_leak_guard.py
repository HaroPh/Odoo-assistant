# backend/src/agents/tool_leak_guard.py
"""Chặn tất định việc lộ tên tool MCP thô ra user qua câu trả lời cuối của
agentic skill (tier-2) — bổ sung cho SOP_PROMPT (dặn "không nhắc tên tool"
chỉ có xác suất tuân theo, model 8-9B local có thể lệch). Marker list dùng
chung giữa production (agentic_context_sync.py) và live-verify test script
(backend/tests/live_verify_common.py import lại từ đây — một nguồn duy
nhất, không định nghĩa trùng)."""

TOOL_NAME_LEAK_MARKERS = (
    "receive_order", "flag_order_for_review", "deliver_order",
    "create_discount_quote", "get_purchase_order_detail",
    "get_sale_order_detail", "ask_human(",
)

# Generic — node dùng chung này chạy sau CẢ 3 skill (warehouse/delivery/
# discount), không được đặc thù hoá theo 1 skill. Câu bridge đặc thù
# "điều chỉnh tồn kho" là trách nhiệm của SOP_PROMPT (warehouse_receiving),
# KHÔNG phải của scrub — scrub chỉ là lưới an toàn chung, cùng triết lý
# SAFE_MSG/RECURSION_MSG đã có (degrade về canned message khi có sự cố,
# không cố sửa câu chữ mô hình đã lỡ viết).
TOOL_LEAK_FALLBACK_MSG = (
    "Xin lỗi, tôi gặp vướng mắc khi xử lý yêu cầu này. Bạn vui lòng mô tả "
    "cụ thể hơn bạn muốn thực hiện thao tác gì?"
)


def has_tool_leak(text: str) -> list[str]:
    low = text.lower()
    return [m for m in TOOL_NAME_LEAK_MARKERS if m.lower() in low]
