# backend/src/agents/edit_order.py
"""Deterministic draft-order edit coordinator (sale = update_quotation_lines,
purchase = update_rfq_lines). Only draft/sent orders are line-edited; a confirmed
order instead offers to post an internal review note (message_post). Mirrors
create_order.py: re-entrant via interrupt-replay, no LLM, cfg-parameterized."""

import os
from dataclasses import dataclass
from typing import Callable

from langchain_core.messages import AIMessage

from .create_order import (
    _interrupt, _msg, _by_id, _disambig_q, _ttl_expiry,
    resolve_entity_for_order, WRITE_DISABLED_MSG,
)
from .tool_result import parse_write_result
from .working_context import derive_working_context
from ..erp_query import sales, inventory, purchase

FLAG_TOOL = "flag_order_for_review"
_DRAFT_STATES = ("draft", "sent")


# Late-binding wrappers so tests monkeypatch the erp_query module attribute
# (mirrors _find_customer in create_order.py).
def _get_sale_detail(ref):
    return sales.get_sale_order_detail(ref)


def _get_purchase_detail(ref):
    return purchase.get_purchase_order_detail(ref)


@dataclass(frozen=True)
class EditCfg:
    get_detail: Callable   # (ref) -> erp_query envelope; data={"order","lines"}
    model: str             # "sale.order" | "purchase.order"
    order_label: str       # "báo giá" | "đơn mua"
    price: bool            # sale shows subtotal on added lines; purchase qty only
    qty_field: str         # "product_uom_qty" | "product_qty"  (Invariant #1)
    tool_name: str         # "update_quotation_lines" | "update_rfq_lines"


SALE_EDIT_CFG = EditCfg(_get_sale_detail, "sale.order", "báo giá",
                        True, "product_uom_qty", "update_quotation_lines")
PURCHASE_EDIT_CFG = EditCfg(_get_purchase_detail, "purchase.order", "đơn mua",
                            False, "product_qty", "update_rfq_lines")


def _line_name(line) -> str:
    return (line.get("product_id") or [0, "?"])[1]


def _match_line(lines, ref, qty_field):
    """Match a change's product name to an existing order line (Invariant #5).
    ("ok", line) | ("none", None) | ("ambiguous", [{"id","name"}])."""
    r = (ref or "").strip().lower()
    exact = [l for l in lines if _line_name(l).strip().lower() == r]
    subs = [l for l in lines if r and r in _line_name(l).strip().lower()]
    hits = exact or subs
    if not hits:
        return "none", None
    if len(hits) == 1:
        return "ok", hits[0]
    opts = [{"id": l["id"], "name": f"{_line_name(l)} (SL {l[qty_field]:g})"}
            for l in hits]
    return "ambiguous", opts


def _flag_note(changes) -> str:
    """Deterministic Vietnamese note summarizing the requested edits."""
    parts = []
    for c in changes:
        act, prod, qty = c.get("action"), c.get("product") or "?", c.get("qty")
        has_qty = isinstance(qty, (int, float))
        if act == "add":
            parts.append(f"thêm {prod} × {qty:g}" if has_qty else f"thêm {prod}")
        elif act == "remove":
            parts.append(f"xóa {prod}")
        elif act == "set_qty":
            parts.append(f"đổi SL {prod} → {qty:g}" if has_qty else f"đổi SL {prod}")
    return "Đề nghị sửa: " + ("; ".join(parts) if parts else "(không rõ)") + "."


def _render_diff(cfg, name, partner, adds, removes, sets, note: str = "") -> str:
    body = [f"  + Thêm: {a}" for a in adds]
    body += [f"  - Xóa: {r}" for r in removes]
    body += [f"  ~ Đổi SL: {s}" for s in sets]
    return (f"Sửa {cfg.order_label} {name} ({partner}):\n"
            + "\n".join(body) + note + "\nXác nhận? (có / không)")


def make_edit_order_node(tools, cfg: EditCfg):
    """Deterministic draft-order edit coordinator parameterized by cfg."""
    by_name = {t.name: t for t in tools}

    async def edit_node(state):
        if os.environ.get("WRITE_ACTIONS_ENABLED", "false").lower() != "true":
            return _msg(WRITE_DISABLED_MSG)

        action = state.get("pending_action") or {}
        args = action.get("args") or {}
        order_ref = args.get("order_ref") or ""
        changes = args.get("changes") or []
        if not changes:
            return _msg("Vui lòng cho biết cần sửa gì trên đơn "
                        "(thêm/xóa/đổi số lượng dòng nào).")

        env = cfg.get_detail(order_ref)
        if env.get("status") != "success":
            return _msg(env.get("display") or f"Không tìm thấy đơn '{order_ref}'.")
        data = env.get("data") or {}
        order = data.get("order") or {}
        lines = data.get("lines") or []
        name = order.get("name") or order_ref
        partner_id, partner = (order.get("partner_id") or [0, "?"])

        # Confirmed order → offer an internal review note instead (Invariant #2).
        if order.get("state") not in _DRAFT_STATES:
            ok = _interrupt({"kind": "confirm",
                             "question": (f"Đơn {name} đã xác nhận, không thể sửa "
                                          f"trực tiếp. Bạn có muốn ghi chú nội bộ "
                                          f"trên đơn để báo quản lý không? "
                                          f"(có / không)"),
                             "expires_at": _ttl_expiry()})
            if not ok:
                return _msg("Đã hủy.")
            tool = by_name.get(FLAG_TOOL)
            if tool is None:
                return _msg("Công cụ ghi chú không khả dụng.")
            try:
                result = await tool.ainvoke({"model": cfg.model, "order_ref": name,
                                             "note": _flag_note(changes)})
            except Exception as e:  # noqa: BLE001
                return _msg(f"Lỗi khi ghi chú: {e}")
            display, fenv = parse_write_result(result)
            upd = {"messages": [AIMessage(content=display)], "pending_action": None,
                   "last_write": {"tool": FLAG_TOOL, **fenv} if fenv else None}
            wc = derive_working_context(fenv)
            if wc:
                upd["working_context"] = wc
            return upd

        # Draft/sent → resolve each change into an ID-addressed op.
        ops, adds, removes, sets = [], [], [], []
        for c in changes:
            act = c.get("action")
            if act == "add":
                ref = c.get("product") or ""
                qty = c.get("qty")
                if not isinstance(qty, (int, float)) or isinstance(qty, bool) or qty <= 0:
                    return _msg(f"Số lượng thêm cho '{ref}' phải là số lớn hơn 0.")
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
                ops.append({"op": "add", "product_id": product["id"], "qty": qty})
                if cfg.price:
                    penv = sales.get_product_price(product["id"], partner_id, qty)
                    price = (penv.get("data") or {}).get("price", 0.0) \
                        if penv.get("status") == "success" else 0.0
                    adds.append(f"{product['name']} × {qty:g} = {price * qty:,.0f}")
                else:
                    adds.append(f"{product['name']} × {qty:g}")
            elif act in ("remove", "set_qty"):
                ref = c.get("product") or ""
                mkind, mval = _match_line(lines, ref, cfg.qty_field)
                if mkind == "none":
                    return _msg(f"Không tìm thấy dòng '{ref}' trong đơn.")
                if mkind == "ambiguous":
                    chosen = _interrupt({"kind": "disambiguation",
                                         "question": _disambig_q(f"dòng '{ref}'", mval),
                                         "options": mval, "expires_at": _ttl_expiry()})
                    line = _by_id(lines, chosen)
                    if line is None:
                        return _msg("Đã hủy.")
                else:
                    line = mval
                if act == "remove":
                    ops.append({"op": "remove", "line_id": line["id"]})
                    removes.append(f"{_line_name(line)} (SL {line[cfg.qty_field]:g})")
                else:
                    qty = c.get("qty")
                    if not isinstance(qty, (int, float)) or isinstance(qty, bool) or qty <= 0:
                        return _msg(f"Số lượng mới cho '{ref}' phải là số lớn hơn 0.")
                    ops.append({"op": "set_qty", "line_id": line["id"], "qty": qty})
                    sets.append(f"{_line_name(line)}: {line[cfg.qty_field]:g} → {qty:g}")
            else:
                return _msg(f"Thao tác không hỗ trợ: '{act}'.")

        note = (state.get("pending_action") or {}).get("chain_note") or ""
        confirmed = _interrupt({"kind": "confirm",
                                "question": _render_diff(cfg, name, partner,
                                                         adds, removes, sets, note),
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy.")

        tool = by_name.get(cfg.tool_name)
        if tool is None:
            return _msg("Công cụ sửa đơn không khả dụng.")
        try:
            result = await tool.ainvoke({"order_ref": name, "ops": ops})
        except Exception as e:  # noqa: BLE001
            return _msg(f"Lỗi khi sửa đơn: {e}")
        display, eenv = parse_write_result(result)
        upd = {"messages": [AIMessage(content=display)], "pending_action": None,
               "last_write": {"tool": cfg.tool_name, **eenv} if eenv else None}
        wc = derive_working_context(eenv)
        if wc:
            upd["working_context"] = wc
        return upd

    return edit_node
