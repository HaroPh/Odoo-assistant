# backend/src/agents/create_order.py
"""Deterministic create-sales-order coordinator (the `create_order` node) and its
pure helpers. Reads come from erp_query; the created order is built from resolved
IDs so it matches the confirmed draft. Within-flow memory is LangGraph
interrupt-replay — this node is re-entrant and holds no persistent state."""

import os
import time

from langchain_core.messages import AIMessage
from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .tool_result import _tool_result_text
from ..erp_query import sales, inventory

WRITE_DISABLED_MSG = ("Tính năng ghi (tạo/sửa đơn hàng, cập nhật tồn kho) "
                      "chưa được kích hoạt trong phiên bản này.")


def resolve_entity_for_order(envelope: dict, ref: str):
    """Map a resolve_entity envelope to a coordinator decision.

    Returns one of:
      ("ok", {"id","name"})       — a single confident entity
      ("ambiguous", [{"id","name"}, ...]) — the user must choose
      ("none", None)              — no match
      ("error", "<message>")      — lookup failed
    """
    if envelope.get("status") != "success":
        return "error", envelope.get("display") or "Lỗi tra cứu."
    data = envelope.get("data") or {}
    matches = data.get("matches") or []
    if not matches:
        return "none", None
    if data.get("needs_disambiguation"):
        return "ambiguous", [{"id": m["id"], "name": m["name"]} for m in matches]
    exact = [m for m in matches if (m["name"] or "").strip().lower() == ref.strip().lower()]
    chosen = exact[0] if exact else matches[0]
    return "ok", {"id": chosen["id"], "name": chosen["name"]}


def render_draft(customer: dict, lines: list, total: float) -> str:
    body = "\n".join(
        f"  - {l['name']} × {l['qty']:g} = {l['subtotal']:,.0f}" for l in lines)
    return (f"Báo giá cho {customer['name']}:\n{body}\n"
            f"Tổng: {total:,.0f}\nXác nhận tạo báo giá? (có / không)")


def _ttl_expiry() -> float:
    return time.time() + int(os.environ.get("CONFIRMATION_TTL_SECONDS", "300"))


def _by_id(options, chosen):
    for o in options:
        if o["id"] == chosen:
            return o
    return None


def _msg(text: str) -> dict:
    return {"messages": [AIMessage(content=text)]}


def make_create_order_node(llm, tools):
    """Deterministic create-quotation coordinator. Re-entrant: on each resume
    LangGraph re-runs this node, replays the stored interrupt values, and the
    idempotent erp_query re-resolution rebuilds the same draft (no LLM here)."""
    by_name = {t.name: t for t in tools}

    async def create_order(state: ERPAgentState) -> dict:
        if os.environ.get("WRITE_ACTIONS_ENABLED", "false").lower() != "true":
            return _msg(WRITE_DISABLED_MSG)

        action = state.get("pending_action") or {}
        args = action.get("args") or {}
        customer_ref = args.get("partner_name") or ""
        raw_lines = args.get("lines") or []

        # 1) Resolve customer
        kind, val = resolve_entity_for_order(sales.find_customer(customer_ref), customer_ref)
        if kind == "error":
            return _msg(val)
        if kind == "none":
            return _msg(f"Không tìm thấy khách hàng '{customer_ref}'.")
        if kind == "ambiguous":
            chosen = _interrupt({"kind": "disambiguation",
                                 "question": _disambig_q("khách hàng", val),
                                 "options": val, "expires_at": _ttl_expiry()})
            customer = _by_id(val, chosen)
            if customer is None:
                return _msg("Đã hủy tạo báo giá.")
        else:
            customer = val

        # 2) Resolve + price each line
        priced = []
        for line in raw_lines:
            ref = line.get("product") or ""
            qty = line.get("qty") or 0
            pkind, pval = resolve_entity_for_order(inventory.find_product(ref), ref)
            if pkind == "error":
                return _msg(pval)
            if pkind == "none":
                return _msg(f"Không tìm thấy sản phẩm '{ref}'.")
            if pkind == "ambiguous":
                chosen = _interrupt({"kind": "disambiguation",
                                     "question": _disambig_q(f"sản phẩm '{ref}'", pval),
                                     "options": pval, "expires_at": _ttl_expiry()})
                product = _by_id(pval, chosen)
                if product is None:
                    return _msg("Đã hủy tạo báo giá.")
            else:
                product = pval
            penv = sales.get_product_price(product["id"], customer["id"], qty)
            price = (penv.get("data") or {}).get("price", 0.0) \
                if penv.get("status") == "success" else 0.0
            priced.append({"product_id": product["id"], "name": product["name"],
                           "qty": qty, "unit_price": price, "subtotal": price * qty})

        # 3) Confirm the priced draft
        total = sum(l["subtotal"] for l in priced)
        confirmed = _interrupt({"kind": "confirm",
                                "question": render_draft(customer, priced, total),
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy tạo báo giá.")

        # 4) Create via the ID path
        tool = by_name.get("create_quotation")
        if tool is None:
            return _msg("Công cụ tạo báo giá không khả dụng.")
        try:
            result = await tool.ainvoke(
                {"partner_id": customer["id"],
                 "lines": [{"product_id": l["product_id"], "qty": l["qty"]} for l in priced]})
        except Exception as e:  # noqa: BLE001 — never crash the graph
            return _msg(f"Lỗi khi tạo báo giá: {e}")
        return _msg(_tool_result_text(result))

    return create_order


def _disambig_q(label: str, options) -> str:
    listing = "\n".join(f"  {i}. {o['name']} (ID {o['id']})"
                        for i, o in enumerate(options, 1))
    return f"Có nhiều {label} phù hợp, bạn chọn mục nào?\n{listing}"
