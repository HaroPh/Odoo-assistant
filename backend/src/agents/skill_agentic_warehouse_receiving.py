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
6. Nếu QC đạt: PHẢI dùng ask_human hỏi xác nhận lần cuối trước khi ghi vào
   hệ thống (ví dụ: "Xác nhận nhận hàng đơn mua {mã đơn}?"). CHỈ khi người
   dùng xác nhận đồng ý, mới được gọi receive_order.

Quy tắc bắt buộc, không được vi phạm:
- Không được tự suy đoán số lượng thực nhận hoặc kết quả QC thay cho việc
  hỏi qua ask_human.
- Không được gọi receive_order nếu số lượng không khớp HOẶC QC không đạt.
- Không được gọi receive_order mà không hỏi xác nhận lần cuối (bước 6)
  trước đó.
- Không được bịa mã đơn mua hoặc số liệu không có trong hội thoại hoặc kết
  quả tra cứu."""


@tool
def ask_human(question: str) -> str:
    """Hỏi người dùng một câu hỏi mở và chờ câu trả lời. Dùng khi cần thông
    tin chỉ con người mới biết được (số lượng đã đếm, kết quả kiểm tra chất
    lượng, xác nhận trước khi ghi vào hệ thống...). KHÔNG được tự suy đoán
    thay cho việc hỏi."""
    return _interrupt({"kind": "free_text", "question": question})


def _build_tools(mcp_tools):
    by_name = {t.name: t for t in mcp_tools}
    read_tools = {t.name: t for t in build_erp_query_tools()}
    tools = [ask_human, read_tools["get_purchase_order_detail"]]
    for name in ("receive_order", "flag_order_for_review"):
        tool = by_name.get(name)
        if tool is not None:
            tools.append(tool)
    return tools


def make_node(llm, mcp_tools):
    """Returns the compiled create_agent graph directly — this IS the node
    (see module docstring), not a function that builds/calls one
    internally."""
    return create_agent(llm, _build_tools(mcp_tools), system_prompt=SOP_PROMPT)
