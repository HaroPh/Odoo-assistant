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

from .agentic_gate import REFUSED_MSG, _confirm_write, ask_human
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
4. Thông báo kết quả cho người dùng bằng đúng nội dung câu "display" trong
   kết quả deliver_order trả về — không thêm suy đoán, không tự diễn giải
   khác đi, không chép JSON thô ra ngoài.

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
