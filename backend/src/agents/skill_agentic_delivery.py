# backend/src/agents/skill_agentic_delivery.py
"""Agentic delivery SOP skill: xác nhận giao hàng (deliver_order) cho đơn
bán đã xác nhận, driven bởi create_agent ReAct loop — mirror trực tiếp
skill_agentic_warehouse_receiving.py (đã merge, đã kiểm chứng qua Probe B
8/8 + probe cạnh biên refusal 2/2). Xem
docs/superpowers/specs/2026-07-16-agentic-delivery-design.md.

The returned CompiledStateGraph from make_node() MUST be added directly as
a node in the outer graph (g.add_node(name, make_node(...))), never wrapped
in a hand-written async function — đó là điều kiện để interrupt() bên
trong tool của nó compose đúng với checkpointer của outer graph."""

from langchain.agents import create_agent
from langchain_core.tools import tool
from langgraph.types import interrupt as _interrupt

from .create_order import _ttl_expiry
from ..erp_query.tools import build_erp_query_tools

TRIGGERS = ("giao hang cho don ban", "xuat kho cho don ban", "giao hang theo don")

SOP_PROMPT = """Bạn là trợ lý kho, thực hiện quy trình giao hàng cho đơn bán.
Bạn có các công cụ: get_sale_order_detail (tra chi tiết đơn bán), ask_human
(hỏi người dùng và chờ trả lời), deliver_order (xác nhận giao hàng vào Odoo).

Quy trình, làm đúng thứ tự:
1. Xác định mã đơn bán cần giao hàng từ yêu cầu của người dùng. Nếu tin nhắn
   chưa nêu rõ mã đơn, dùng ask_human để hỏi.
2. Dùng get_sale_order_detail để tra thông tin đơn (khách hàng, mặt hàng) —
   dùng để có ngữ cảnh, không cần hỏi lại người dùng số liệu này.
3. Gọi deliver_order để giao hàng.
4. Báo lại NGUYÊN VĂN kết quả deliver_order trả về cho người dùng — công cụ
   đã tự xử lý đủ các tình huống (không có phiếu cần giao / chưa sẵn sàng /
   cần xử lý tay / thành công), không tự diễn giải thêm hay suy đoán khác
   với nội dung đó.

Quy tắc bắt buộc, không được vi phạm:
- Không được bịa mã đơn bán hoặc số liệu không có trong hội thoại hoặc kết
  quả tra cứu.
- Không được tự ý gọi deliver_order khi chưa xác định rõ mã đơn.
- Khi bạn gọi deliver_order, hệ thống sẽ TỰ ĐỘNG hỏi người dùng xác nhận
  trước khi ghi — bạn KHÔNG cần tự hỏi xác nhận trước bằng ask_human. Nếu
  công cụ trả về "Người dùng TỪ CHỐI xác nhận", không thử gọi lại ngay — hỏi
  người dùng muốn làm gì tiếp.
- KHÔNG tự động đề xuất hoặc thực hiện bước tiếp theo (tạo hóa đơn) sau khi
  giao hàng xong — dừng lại ở đó, chờ yêu cầu mới từ người dùng."""

REFUSED_MSG = ("Người dùng TỪ CHỐI xác nhận — KHÔNG thực hiện thao tác. "
               "Hãy hỏi người dùng muốn làm gì tiếp.")


@tool
def ask_human(question: str) -> str:
    """Hỏi người dùng một câu hỏi mở và chờ câu trả lời. Dùng khi cần thông
    tin chỉ con người mới biết được (mã đơn bán chưa nêu rõ...). KHÔNG được
    tự suy đoán thay cho việc hỏi."""
    return _interrupt({"kind": "free_text", "question": question})


def _confirm_write(question: str) -> bool:
    """Cổng xác nhận cứng tại ranh giới tool ghi — model không bao giờ thấy
    tool ghi thô nên không có đường vòng nào bỏ qua cổng này. kind="confirm"
    đi qua erp_agent._decide_resume (phân loại có/không → resume bool); để
    quá TTL → resume False. Chỉ True tuyệt đối mới cho ghi."""
    answer = _interrupt({"kind": "confirm", "question": question,
                         "expires_at": _ttl_expiry()})
    return answer is True


def _build_tools(mcp_tools):
    by_name = {t.name: t for t in mcp_tools}
    read_tools = {t.name: t for t in build_erp_query_tools()}
    tools = [ask_human, read_tools["get_sale_order_detail"]]

    deliver = by_name.get("deliver_order")
    if deliver is not None:
        @tool("deliver_order")
        async def deliver_order_gated(order_ref: str) -> str:
            """Xác nhận giao hàng vào Odoo cho một đơn bán ĐÃ XÁC NHẬN.
            Hệ thống sẽ tự hỏi người dùng xác nhận trước khi ghi."""
            if not _confirm_write(f"Xác nhận GIAO HÀNG cho đơn bán {order_ref}?"):
                return REFUSED_MSG
            return await deliver.ainvoke({"order_ref": order_ref})
        tools.append(deliver_order_gated)

    return tools


def make_node(llm, mcp_tools):
    """Returns the compiled create_agent graph directly (xem module
    docstring)."""
    return create_agent(llm, _build_tools(mcp_tools), system_prompt=SOP_PROMPT)
