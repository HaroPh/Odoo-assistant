"""Agentic warehouse_receiving SOP skill (tier-2): nhập kho theo quy trình,
driven bởi create_agent ReAct loop với confirm-gate tại tool boundary. Xem
docs/superpowers/specs/2026-07-15-agentic-wr-guardrails-design.md và
docs/superpowers/specs/2026-07-17-sop-tier1-handoff-design.md (nhánh "không
có PO", finding #4 — SOP không có lối thoát về tier-1 inventory_adjustment).

The returned CompiledStateGraph from make_node() MUST be added directly as
a node in the outer graph (g.add_node(name, make_node(...))), never wrapped
in a hand-written async function — that is what makes interrupt() calls
inside its tools compose correctly with the outer graph's checkpointer."""

from langchain.agents import create_agent
from langchain_core.tools import tool

from .agentic_gate import REFUSED_MSG, _confirm_write, ask_human
from ..erp_query.tools import build_erp_query_tools

TRIGGERS = ("quy trinh nhap kho", "nhap kho theo quy trinh")

# Câu bridge cố định sang tier-1 inventory_adjustment — model được dặn trả
# ĐÚNG NGUYÊN VĂN chuỗi này (không tự diễn giải) để giảm biến thiên. Route
# "điều chỉnh tồn kho <tên> về <số lượng>" đã verify KHÔNG khớp TRIGGERS ở
# trên nên tin nhắn kế tiếp của user route đúng qua erp_write_planner ->
# inventory_adjustment (backend/src/agents/inventory_write.py) — không cần
# đổi gì ở graph.py/agentic_registry.py.
NO_PO_BRIDGE_MSG = (
    "Quy trình nhập kho này yêu cầu có đơn mua (PO). Nếu bạn chỉ cần cập "
    "nhật số lượng tồn kho trực tiếp, hãy nói ví dụ: 'điều chỉnh tồn kho "
    "<tên sản phẩm> về <số lượng>' — tôi sẽ thực hiện ngay."
)

SOP_PROMPT = f"""Bạn là trợ lý kho, thực hiện quy trình nhập kho. Bạn có các
công cụ: get_purchase_order_detail (tra chi tiết đơn mua), ask_human (hỏi
người dùng và chờ trả lời), receive_order (xác nhận nhận hàng vào Odoo),
flag_order_for_review (ghi chú nội bộ lên đơn khi có bất thường — dùng thay
vì receive_order khi số lượng không khớp).

Quy trình, làm đúng thứ tự:
1. Xác định mã đơn mua cần nhập kho từ yêu cầu của người dùng. Nếu tin nhắn
   chưa nêu rõ mã đơn, dùng ask_human để hỏi.
2. Nếu người dùng cho biết KHÔNG CÓ đơn mua (chưa tạo, không định tạo, muốn
   nhập thẳng không qua PO): DỪNG NGAY quy trình này, không hỏi thêm gì về
   số lượng hay QC, không nhắc tên bất kỳ công cụ nào. Trả lời đúng nguyên
   văn (không diễn giải khác, không thêm bớt): "{NO_PO_BRIDGE_MSG}"
3. Dùng ask_human hỏi người dùng đã kiểm đếm hàng chưa và số lượng thực
   nhận (tổng tất cả mặt hàng, một con số) là bao nhiêu.
4. Dùng get_purchase_order_detail để tra số lượng đã đặt trên đơn mua đó.
5. So sánh số lượng thực nhận (bước 3) với tổng số lượng trên đơn (bước 4):
   - Nếu KHỚP: tiếp tục bước 6.
   - Nếu KHÔNG KHỚP (thiếu hoặc thừa): PHẢI dùng flag_order_for_review để
     ghi chú rõ tình trạng (thiếu bao nhiêu / thừa bao nhiêu). TUYỆT ĐỐI
     KHÔNG được gọi receive_order trong trường hợp này. Dừng quy trình,
     báo lại kết quả cho người dùng.
6. Nếu số lượng khớp, dùng ask_human hỏi bộ phận QC đã kiểm tra chất lượng
   xong chưa và kết quả (đạt hay không đạt).
   - Nếu KHÔNG ĐẠT: KHÔNG được gọi receive_order. Báo lại cho người dùng
     là hàng không đạt QC, chờ xử lý theo quy trình trả hàng.
7. Nếu QC đạt: gọi receive_order. Khi bạn gọi công cụ ghi (receive_order
   hoặc flag_order_for_review), hệ thống sẽ TỰ ĐỘNG hỏi người dùng xác nhận
   trước khi ghi — bạn KHÔNG cần tự hỏi xác nhận trước bằng ask_human. Nếu
   công cụ trả về "Người dùng TỪ CHỐI xác nhận", không thử gọi lại ngay —
   hỏi người dùng muốn làm gì tiếp.

Quy tắc bắt buộc, không được vi phạm:
- Không được tự suy đoán số lượng thực nhận hoặc kết quả QC thay cho việc
  hỏi qua ask_human.
- Không được gọi receive_order nếu số lượng không khớp HOẶC QC không đạt.
- Không được bịa mã đơn mua hoặc số liệu không có trong hội thoại hoặc kết
  quả tra cứu."""


def _build_tools(mcp_tools):
    by_name = {t.name: t for t in mcp_tools}
    read_tools = {t.name: t for t in build_erp_query_tools()}
    tools = [ask_human, read_tools["get_purchase_order_detail"]]

    receive = by_name.get("receive_order")
    if receive is not None:
        @tool("receive_order")
        async def receive_order_gated(order_ref: str) -> str:
            """Xác nhận nhận hàng vào Odoo cho một đơn mua ĐÃ XÁC NHẬN.
            Hệ thống sẽ tự hỏi người dùng xác nhận trước khi ghi."""
            if not _confirm_write(f"Xác nhận NHẬN HÀNG cho đơn mua {order_ref}?"):
                return REFUSED_MSG
            return await receive.ainvoke({"order_ref": order_ref})
        tools.append(receive_order_gated)

    flag = by_name.get("flag_order_for_review")
    if flag is not None:
        @tool("flag_order_for_review")
        async def flag_order_for_review_gated(order_ref: str, note: str) -> str:
            """Ghi chú nội bộ lên đơn mua khi có bất thường (lệch số lượng...)
            để phòng mua hàng rà soát — dùng thay receive_order khi số lượng
            không khớp. Hệ thống sẽ tự hỏi người dùng xác nhận trước khi ghi."""
            if not _confirm_write(
                    f'Xác nhận GHI CHÚ lên đơn mua {order_ref}: "{note}"?'):
                return REFUSED_MSG
            return await flag.ainvoke({"model": "purchase.order",
                                       "order_ref": order_ref, "note": note})
        tools.append(flag_order_for_review_gated)

    return tools


def make_node(llm, mcp_tools):
    """Returns the compiled create_agent graph directly — this IS the node
    (see module docstring), not a function that builds/calls one
    internally."""
    return create_agent(llm, _build_tools(mcp_tools), system_prompt=SOP_PROMPT)
