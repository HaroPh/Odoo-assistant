# backend/src/agents/skill_agentic_discount_quote.py
"""Agentic discount-quote SOP skill: báo giá có chiết khấu theo cấp khách,
driven bởi create_agent ReAct loop — skill agentic thứ 3, đồng thời là bản
migrate của skill tất định cuối cùng (Đợt 3: skills.py/skill_extract nghỉ
hưu). Xem docs/superpowers/specs/2026-07-17-tier2-retirement-design.md.

Bất biến tiền bạc: % chiết khấu và đơn giá LUÔN tính trong code
(compute_discount_pct + get_product_price) — model chỉ gom tham số
(khách, dòng hàng, cấp khách), không bao giờ tính hay truyền số tiền.

The returned CompiledStateGraph from make_node() MUST be added directly as
a node in the outer graph (g.add_node(name, make_node(...))), never wrapped
in a hand-written async function — đó là điều kiện để interrupt() bên
trong tool của nó compose đúng với checkpointer của outer graph."""

from langchain.agents import create_agent
from langchain_core.tools import tool

from .agentic_gate import REFUSED_MSG, _confirm_write, ask_human
from .create_order import resolve_entity_for_order
from .skill_gate import _fold
from ..erp_query import sales, inventory

TRIGGERS = ("bao gia chiet khau", "bao gia kem chiet khau", "bao gia theo cap khach")

TIER_PCT = {"thuong": 0.0, "than_thiet": 0.05, "doi_tac": 0.10}

# Nhận cả id lẫn cách gõ tự nhiên (đã _fold): model 8-9B hay trả nhãn tiếng
# Việt thay vì id — map tất định, sai thì trả lỗi liệt kê, không đoán.
_TIER_ALIASES = {
    "thuong": "thuong", "khach thuong": "thuong", "binh thuong": "thuong",
    "than thiet": "than_thiet", "than_thiet": "than_thiet",
    "khach than thiet": "than_thiet",
    "doi tac": "doi_tac", "doi_tac": "doi_tac",
    "doi tac chien luoc": "doi_tac", "chien luoc": "doi_tac",
}

TIER_INVALID_MSG = ("Cấp khách không hợp lệ. Ba cấp hợp lệ: Thường / Thân thiết / "
                    "Đối tác chiến lược. Hãy hỏi lại người dùng bằng ask_human.")


def compute_discount_pct(tier_id: str, order_total: float) -> float:
    base = TIER_PCT[tier_id]
    bonus = 0.02 if order_total >= 50_000_000 else 0.0
    # round(): base+bonus in raw IEEE-754 float can land off-integer-percent
    # (e.g. 0.10 + 0.02 == 0.12000000000000001) — all tier/bonus values are
    # whole percentage points, so round to 2dp before the cap comparison.
    return min(round(base + bonus, 2), 0.15)


def _render_discount_draft(partner, lines, pct) -> str:
    body = "\n".join(f"  - {l['name']} × {l['qty']:g} = {l['subtotal']:,.0f}"
                     for l in lines)
    total_before = sum(l["subtotal"] for l in lines)
    total_after = total_before * (1 - pct)
    return (f"Báo giá cho {partner['name']}:\n{body}\n"
            f"Tổng trước chiết khấu: {total_before:,.0f}\n"
            f"Chiết khấu: {pct * 100:g}%\n"
            f"Tổng sau chiết khấu: {total_after:,.0f}\n"
            f"Xác nhận? (có / không)")


SOP_PROMPT = """Bạn là trợ lý bán hàng, thực hiện quy trình báo giá có chiết khấu
theo cấp khách hàng. Bạn có các công cụ: ask_human (hỏi người dùng và chờ trả
lời), create_discount_quote (tạo báo giá có chiết khấu vào Odoo — hệ thống TỰ
tính đơn giá và % chiết khấu trong code).

Quy trình, làm đúng thứ tự:
1. Xác định từ yêu cầu của người dùng: tên khách hàng và danh sách sản phẩm +
   số lượng. Nếu thiếu bất kỳ thông tin nào, dùng ask_human để hỏi.
2. Dùng ask_human hỏi khách hàng này thuộc cấp nào, nêu rõ 3 lựa chọn:
   Thường / Thân thiết / Đối tác chiến lược.
3. Gọi create_discount_quote với customer, lines, tier đã gom được.
4. Nếu công cụ trả về danh sách nhiều khách hàng/sản phẩm trùng tên: dùng
   ask_human cho người dùng chọn đúng, rồi gọi lại create_discount_quote với
   tên đã chọn.
5. Thông báo kết quả cho người dùng bằng đúng nội dung câu "display" trong kết
   quả create_discount_quote trả về — không thêm suy đoán, không tự diễn giải
   khác đi, không chép JSON thô ra ngoài.

Quy tắc bắt buộc, không được vi phạm:
- TUYỆT ĐỐI không tự tính, không hứa hẹn, không nêu % chiết khấu hay giá tiền —
  mọi con số tiền do hệ thống tính trong code và sẽ hiện trong câu xác nhận.
- Không được bịa tên khách hàng, sản phẩm, số lượng hay cấp khách không có
  trong hội thoại.
- Khi bạn gọi create_discount_quote, hệ thống sẽ TỰ ĐỘNG hỏi người dùng xác
  nhận (kèm đầy đủ số tiền) trước khi ghi — bạn KHÔNG cần tự hỏi xác nhận
  trước bằng ask_human. Nếu công cụ trả về "Người dùng TỪ CHỐI xác nhận",
  không thử gọi lại ngay — hỏi người dùng muốn làm gì tiếp.
- KHÔNG tự động đề xuất hoặc thực hiện bước tiếp theo (xác nhận báo giá) sau
  khi tạo xong — dừng lại ở đó, chờ yêu cầu mới từ người dùng."""


def _build_tools(mcp_tools):
    by_name = {t.name: t for t in mcp_tools}
    tools = [ask_human]

    create = by_name.get("create_quotation")
    if create is not None:
        @tool("create_discount_quote")
        async def create_discount_quote_gated(customer: str, lines: list[dict],
                                              tier: str) -> str:
            """Tạo báo giá có chiết khấu theo cấp khách vào Odoo. Hệ thống tự
            tính đơn giá + % chiết khấu trong code và tự hỏi người dùng xác
            nhận trước khi ghi.

            Args:
                customer: Tên khách hàng.
                lines: Danh sách dòng hàng, mỗi dòng {"product": "<tên>", "qty": <số>}.
                tier: Cấp khách — "thuong" | "than_thiet" | "doi_tac".
            """
            tier_id = _TIER_ALIASES.get(_fold(tier).strip())
            if tier_id is None:
                return TIER_INVALID_MSG
            if not str(customer or "").strip():
                return "Chưa có tên khách hàng. Hãy hỏi người dùng bằng ask_human."
            if not lines:
                return "Chưa có dòng hàng nào. Hãy hỏi người dùng sản phẩm + số lượng."

            kind, val = resolve_entity_for_order(sales.find_customer(customer), customer)
            if kind == "error":
                return val
            if kind == "none":
                return f"Không tìm thấy khách hàng '{customer}'."
            if kind == "ambiguous":
                names = "; ".join(str(o.get("name", "?")) for o in val)
                return (f"Có nhiều khách hàng trùng '{customer}': {names}. "
                        "Hãy hỏi người dùng chọn đúng tên rồi gọi lại.")
            partner = val

            quote_lines = []
            for line in lines:
                ref = str(line.get("product") or "")
                try:
                    qty = float(line.get("qty") or 0)
                except (TypeError, ValueError):
                    return (f"Số lượng không hợp lệ cho '{ref}'. Hãy hỏi lại "
                            "người dùng số lượng (một con số).")
                pkind, pval = resolve_entity_for_order(inventory.find_product(ref), ref)
                if pkind == "error":
                    return pval
                if pkind == "none":
                    return f"Không tìm thấy sản phẩm '{ref}'."
                if pkind == "ambiguous":
                    names = "; ".join(str(o.get("name", "?")) for o in pval)
                    return (f"Có nhiều sản phẩm trùng '{ref}': {names}. "
                            "Hãy hỏi người dùng chọn đúng tên rồi gọi lại.")
                product = pval
                penv = sales.get_product_price(product["id"], partner["id"], qty)
                price = (penv.get("data") or {}).get("price", 0.0) \
                    if penv.get("status") == "success" else 0.0
                quote_lines.append({"product_id": product["id"], "name": product["name"],
                                    "qty": qty, "unit_price": price,
                                    "subtotal": price * qty})

            order_total = sum(l["subtotal"] for l in quote_lines)
            pct = compute_discount_pct(tier_id, order_total)

            if not _confirm_write(_render_discount_draft(partner, quote_lines, pct)):
                return REFUSED_MSG
            try:
                tool_lines = [{"product_id": l["product_id"], "qty": l["qty"],
                               "price_unit": l["unit_price"] * (1 - pct)}
                              for l in quote_lines]
                return await create.ainvoke({"partner_id": partner["id"],
                                             "lines": tool_lines})
            except Exception as e:  # noqa: BLE001 — tool luôn trả text, không phá graph
                return f"Lỗi khi tạo báo giá: {e}"
        tools.append(create_discount_quote_gated)

    return tools


def make_node(llm, mcp_tools):
    """Returns the compiled create_agent graph directly (xem module docstring)."""
    return create_agent(llm, _build_tools(mcp_tools), system_prompt=SOP_PROMPT)
