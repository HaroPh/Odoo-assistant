# backend/src/agents/skill_discount_quote.py
"""discount_quote pilot skill: create a sale quotation with a customer-tier +
order-size discount computed in code, never by an LLM. Node is LLM-free by
design — parameters arrive pre-extracted in pending_action, set by
skills.skill_extract (spec §4.3: a skill node must not call an LLM, since
LangGraph replays a node from the top on every interrupt() resume).
See docs/superpowers/specs/2026-07-15-sop-skill-pilot-design.md §5."""

from langchain_core.messages import AIMessage

from .create_order import (
    _interrupt, _msg, _by_id, _disambig_q, _ttl_expiry,
    resolve_entity_for_order, WRITE_DISABLED_MSG,
)
from .tool_result import parse_write_result
from . import write_gate
from ..erp_query import sales, inventory

TRIGGERS = ("bao gia chiet khau", "bao gia kem chiet khau", "bao gia theo cap khach")

EXTRACT_PROMPT = """Bạn là trợ lý ERP đang trích tham số cho một báo giá có chiết khấu theo cấp khách hàng.

Từ tin nhắn của người dùng, trích tên khách hàng và danh sách sản phẩm + số lượng.

Ví dụ:
- "báo giá chiết khấu cho Cửa hàng ABC, 5 Tủ gỗ và 2 Bàn" →
  {"customer": "Cửa hàng ABC", "lines": [{"product": "Tủ gỗ", "qty": 5}, {"product": "Bàn", "qty": 2}]}
- "làm báo giá theo cấp khách cho Nhà hàng Sen Việt, 10 Ghế" →
  {"customer": "Nhà hàng Sen Việt", "lines": [{"product": "Ghế", "qty": 10}]}

Trả lời CHỈ JSON, không giải thích, không markdown fence:
{"customer": "<tên khách hàng>", "lines": [{"product": "<tên sản phẩm>", "qty": <số>}, ...]}"""

TIER_OPTIONS = [{"id": "thuong", "name": "Thường"},
                {"id": "than_thiet", "name": "Thân thiết"},
                {"id": "doi_tac", "name": "Đối tác chiến lược"}]

TIER_PCT = {"thuong": 0.0, "than_thiet": 0.05, "doi_tac": 0.10}


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


def make_node(tools):
    by_name = {t.name: t for t in tools}

    async def discount_quote(state) -> dict:
        if not write_gate.write_actions_enabled():
            return {**_msg(WRITE_DISABLED_MSG), "pending_action": None}

        args = (state.get("pending_action") or {}).get("args") or {}
        customer_ref = args.get("customer") or ""
        raw_lines = args.get("lines") or []
        if not str(customer_ref).strip():
            return {**_msg("Bạn cần cho biết khách hàng nào để mình tạo báo giá."),
                    "pending_action": None}
        if not raw_lines:
            return {**_msg("Bạn cần cho biết (các) sản phẩm và số lượng để mình tạo báo giá."),
                    "pending_action": None}

        kind, val = resolve_entity_for_order(sales.find_customer(customer_ref), customer_ref)
        if kind == "error":
            return {**_msg(val), "pending_action": None}
        if kind == "none":
            return {**_msg(f"Không tìm thấy khách hàng '{customer_ref}'."),
                    "pending_action": None}
        if kind == "ambiguous":
            chosen = _interrupt({"kind": "disambiguation",
                                 "question": _disambig_q("khách hàng", val),
                                 "options": val, "expires_at": _ttl_expiry()})
            partner = _by_id(val, chosen)
            if partner is None:
                return {**_msg("Đã hủy."), "pending_action": None}
        else:
            partner = val

        lines = []
        for line in raw_lines:
            ref = line.get("product") or ""
            qty = line.get("qty") or 0
            pkind, pval = resolve_entity_for_order(inventory.find_product(ref), ref)
            if pkind == "error":
                return {**_msg(pval), "pending_action": None}
            if pkind == "none":
                return {**_msg(f"Không tìm thấy sản phẩm '{ref}'."), "pending_action": None}
            if pkind == "ambiguous":
                chosen = _interrupt({"kind": "disambiguation",
                                     "question": _disambig_q(f"sản phẩm '{ref}'", pval),
                                     "options": pval, "expires_at": _ttl_expiry()})
                product = _by_id(pval, chosen)
                if product is None:
                    return {**_msg("Đã hủy."), "pending_action": None}
            else:
                product = pval
            penv = sales.get_product_price(product["id"], partner["id"], qty)
            price = (penv.get("data") or {}).get("price", 0.0) \
                if penv.get("status") == "success" else 0.0
            lines.append({"product_id": product["id"], "name": product["name"],
                          "qty": qty, "unit_price": price, "subtotal": price * qty})

        order_total = sum(l["subtotal"] for l in lines)

        chosen_tier = _interrupt({"kind": "disambiguation",
                                  "question": f"Khách {partner['name']} thuộc cấp nào?",
                                  "options": TIER_OPTIONS, "expires_at": _ttl_expiry()})
        if chosen_tier not in TIER_PCT:
            return {**_msg("Đã hủy."), "pending_action": None}

        pct = compute_discount_pct(chosen_tier, order_total)

        confirmed = _interrupt({"kind": "confirm",
                                "question": _render_discount_draft(partner, lines, pct),
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return {**_msg("Đã hủy."), "pending_action": None}

        tool = by_name.get("create_quotation")
        if tool is None:
            return {**_msg("Công cụ tạo báo giá không khả dụng."), "pending_action": None}
        try:
            tool_lines = [{"product_id": l["product_id"], "qty": l["qty"],
                          "price_unit": l["unit_price"] * (1 - pct)} for l in lines]
            result = await tool.ainvoke({"partner_id": partner["id"], "lines": tool_lines})
        except Exception as e:  # noqa: BLE001 — never crash the graph
            return {**_msg(f"Lỗi khi tạo báo giá: {e}"), "pending_action": None}
        display, _env = parse_write_result(result)
        return {"messages": [AIMessage(content=display)], "pending_action": None}

    return discount_quote
