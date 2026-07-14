# backend/src/agents/create_order.py
"""Deterministic order-write coordinator (sale = create_quotation, purchase =
create_rfq) and its pure helpers. Reads come from erp_query; the created order is
built from resolved IDs so it matches the confirmed draft. Within-flow memory is
LangGraph interrupt-replay — the node is re-entrant and holds no persistent state."""

import os
import time
from dataclasses import dataclass
from typing import Callable

from langchain_core.messages import AIMessage
from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .tool_result import parse_write_result
from .working_context import derive_working_context
from . import write_gate
from ..erp_query import sales, inventory, purchase

WRITE_DISABLED_MSG = ("Tính năng ghi (tạo/sửa đơn hàng, cập nhật tồn kho) "
                      "chưa được kích hoạt trong phiên bản này.")


def resolve_entity_for_order(envelope: dict, ref: str):
    """Map a resolve_entity envelope to a coordinator decision:
      ("ok", {"id","name"}) | ("ambiguous", [{"id","name"},...]) | ("none", None)
      | ("error", "<message>")."""
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


def render_draft(partner: dict, lines: list, total, head: str = "Báo giá cho",
                 note: str = "") -> str:
    """Confirm-draft text. total=None → qty-only (purchase); else priced (sale).
    note: dòng chuỗi tự động ("\n\nSau đó tự động: ...") chèn TRƯỚC câu hỏi."""
    if total is None:
        body = "\n".join(f"  - {l['name']} × {l['qty']:g}" for l in lines)
        return f"{head} {partner['name']}:\n{body}{note}\nXác nhận? (có / không)"
    body = "\n".join(
        f"  - {l['name']} × {l['qty']:g} = {l['subtotal']:,.0f}" for l in lines)
    return (f"{head} {partner['name']}:\n{body}\n"
            f"Tổng: {total:,.0f}{note}\nXác nhận? (có / không)")


def _ttl_expiry() -> float:
    return time.time() + int(os.environ.get("CONFIRMATION_TTL_SECONDS", "300"))


def _by_id(options, chosen):
    for o in options:
        if o["id"] == chosen:
            return o
    return None


def _msg(text: str) -> dict:
    return {"messages": [AIMessage(content=text)]}


def _disambig_q(label: str, options) -> str:
    listing = "\n".join(f"  {i}. {o['name']} (ID {o['id']})"
                        for i, o in enumerate(options, 1))
    return f"Có nhiều {label} phù hợp, bạn chọn mục nào?\n{listing}"


# Late-binding resolvers so tests can monkeypatch the erp_query module attribute.
def _find_customer(name):
    return sales.find_customer(name)


def _find_supplier(name):
    return purchase.find_supplier(name)


@dataclass(frozen=True)
class OrderCfg:
    resolve_partner: Callable    # (name) -> resolve_entity envelope
    partner_label: str           # "khách hàng" | "nhà cung cấp"
    price: bool                  # sale prices lines; purchase does not
    tool_name: str               # MCP do-tool
    draft_head: str              # draft heading


SALE_CFG = OrderCfg(_find_customer, "khách hàng", True, "create_quotation", "Báo giá cho")
PURCHASE_CFG = OrderCfg(_find_supplier, "nhà cung cấp", False, "create_rfq", "Đơn mua từ")


def make_order_node(tools, cfg: OrderCfg):
    """Deterministic order coordinator parameterized by cfg. Re-entrant: on resume
    LangGraph replays the stored interrupt values and the idempotent erp_query
    re-resolution rebuilds the same draft (no LLM here)."""
    by_name = {t.name: t for t in tools}

    async def order_node(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return _msg(WRITE_DISABLED_MSG)

        action = state.get("pending_action") or {}
        args = action.get("args") or {}
        partner_ref = args.get("partner_name") or ""
        raw_lines = args.get("lines") or []

        # Finding 1: thiếu thông tin cốt lõi → HỎI, không resolve ref rỗng (Odoo
        # coi "" là wildcard → disambiguation vô nghĩa) hay tạo đơn rỗng.
        if not str(partner_ref).strip():
            return _msg(f"Bạn cần cho biết {cfg.partner_label} nào để mình tạo đơn.")
        if not raw_lines:
            return _msg("Bạn cần cho biết (các) sản phẩm và số lượng để mình tạo đơn.")

        # 1) Resolve partner
        kind, val = resolve_entity_for_order(cfg.resolve_partner(partner_ref), partner_ref)
        if kind == "error":
            return _msg(val)
        if kind == "none":
            return _msg(f"Không tìm thấy {cfg.partner_label} '{partner_ref}'.")
        if kind == "ambiguous":
            chosen = _interrupt({"kind": "disambiguation",
                                 "question": _disambig_q(cfg.partner_label, val),
                                 "options": val, "expires_at": _ttl_expiry()})
            partner = _by_id(val, chosen)
            if partner is None:
                return _msg("Đã hủy.")
        else:
            partner = val

        # 2) Resolve (+ price if sale) each line
        lines = []
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
                    return _msg("Đã hủy.")
            else:
                product = pval
            if cfg.price:
                penv = sales.get_product_price(product["id"], partner["id"], qty)
                price = (penv.get("data") or {}).get("price", 0.0) \
                    if penv.get("status") == "success" else 0.0
                lines.append({"product_id": product["id"], "name": product["name"],
                              "qty": qty, "unit_price": price, "subtotal": price * qty})
            else:
                lines.append({"product_id": product["id"], "name": product["name"],
                              "qty": qty})

        # 3) Confirm the draft
        total = sum(l["subtotal"] for l in lines) if cfg.price else None
        note = (state.get("pending_action") or {}).get("chain_note") or ""
        confirmed = _interrupt({"kind": "confirm",
                                "question": render_draft(partner, lines, total,
                                                         head=cfg.draft_head,
                                                         note=note),
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy.")

        # 4) Create via the ID path
        tool = by_name.get(cfg.tool_name)
        if tool is None:
            return _msg("Công cụ tạo đơn không khả dụng.")
        try:
            tool_lines = [
                ({"product_id": l["product_id"], "qty": l["qty"],
                  "price_unit": l["unit_price"]} if cfg.price
                 else {"product_id": l["product_id"], "qty": l["qty"]})
                for l in lines]
            result = await tool.ainvoke(
                {"partner_id": partner["id"], "lines": tool_lines})
        except Exception as e:  # noqa: BLE001 — never crash the graph
            return _msg(f"Lỗi khi tạo đơn: {e}")
        display, env = parse_write_result(result)
        upd = {"messages": [AIMessage(content=display)],
               "pending_action": None,
               "last_write": {"tool": cfg.tool_name, **env} if env else None}
        wc = derive_working_context(env)
        if wc:
            upd["working_context"] = wc
        return upd

    return order_node


def make_create_order_node(llm, tools):
    """C-1 compatibility wrapper: the sale order coordinator."""
    return make_order_node(tools, SALE_CFG)
