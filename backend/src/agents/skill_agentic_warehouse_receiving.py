"""Agentic experiment: warehouse_receiving SOP driven by an LLM through
create_agent's ReAct tool-calling loop, instead of hard-coded branching
(compare backend/src/agents/skill_warehouse_receiving.py, the deterministic
pilot version of the same SOP). See
docs/superpowers/specs/2026-07-15-agentic-sop-experiment-design.md.

The returned CompiledStateGraph from make_node() MUST be added directly as
a node in the outer graph (g.add_node(name, make_node(...))), never wrapped
in a hand-written async function — that is what makes interrupt() calls
inside its tools compose correctly with the outer graph's checkpointer
(spec §4.2)."""

from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.types import interrupt as _interrupt

from .create_order import _ttl_expiry
from ..erp_query.tools import build_erp_query_tools

SOP_PROMPT = """Bạn là trợ lý kho, thực hiện quy trình nhập kho. Bạn có các
công cụ: get_purchase_order_detail (tra chi tiết đơn mua), ask_human (hỏi
người dùng và chờ trả lời), receive_order (xác nhận nhận hàng vào Odoo),
flag_order_for_review (ghi chú nội bộ lên đơn khi có bất thường — dùng thay
vì receive_order khi số lượng không khớp).

Quy trình, làm đúng thứ tự:
1. Xác định mã đơn mua cần nhập kho từ yêu cầu của người dùng. Nếu tin nhắn
   chưa nêu rõ mã đơn, dùng ask_human để hỏi.
2. Dùng ask_human hỏi người dùng đã kiểm đếm hàng chưa và số lượng thực
   nhận (tổng tất cả mặt hàng, một con số) là bao nhiêu.
3. Dùng get_purchase_order_detail để tra số lượng đã đặt trên đơn mua đó.
4. So sánh số lượng thực nhận (bước 2) với tổng số lượng trên đơn (bước 3):
   - Nếu KHỚP: tiếp tục bước 5.
   - Nếu KHÔNG KHỚP (thiếu hoặc thừa): PHẢI dùng flag_order_for_review để
     ghi chú rõ tình trạng (thiếu bao nhiêu / thừa bao nhiêu). TUYỆT ĐỐI
     KHÔNG được gọi receive_order trong trường hợp này. Dừng quy trình,
     báo lại kết quả cho người dùng.
5. Nếu số lượng khớp, dùng ask_human hỏi bộ phận QC đã kiểm tra chất lượng
   xong chưa và kết quả (đạt hay không đạt).
   - Nếu KHÔNG ĐẠT: KHÔNG được gọi receive_order. Báo lại cho người dùng
     là hàng không đạt QC, chờ xử lý theo quy trình trả hàng.
6. Nếu QC đạt: gọi receive_order. Khi bạn gọi công cụ ghi (receive_order
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


@tool
def ask_human(question: str) -> str:
    """Hỏi người dùng một câu hỏi mở và chờ câu trả lời. Dùng khi cần thông
    tin chỉ con người mới biết được (số lượng đã đếm, kết quả kiểm tra chất
    lượng...). KHÔNG được tự suy đoán thay cho việc hỏi."""
    return _interrupt({"kind": "free_text", "question": question})


REFUSED_MSG = ("Người dùng TỪ CHỐI xác nhận — KHÔNG thực hiện thao tác. "
               "Hãy hỏi người dùng muốn làm gì tiếp.")


def _confirm_write(question: str) -> bool:
    """Cổng xác nhận cứng tại ranh giới tool ghi (spec §4.1-4.2): model không
    bao giờ thấy tool ghi thô nên không có đường vòng nào bỏ qua cổng này —
    đóng lỗ hổng Probe B (model bỏ bước hỏi xác nhận khi bị người dùng ép).
    kind="confirm" đi qua erp_agent._decide_resume: phân loại có/không →
    resume bool; để quá TTL → resume False. Chỉ True tuyệt đối mới cho ghi."""
    answer = _interrupt({"kind": "confirm", "question": question,
                         "expires_at": _ttl_expiry()})
    return answer is True


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
