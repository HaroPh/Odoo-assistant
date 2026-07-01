# backend/src/agents/create_order.py
"""Deterministic create-sales-order coordinator (the `create_order` node) and its
pure helpers. Reads come from erp_query; the created order is built from resolved
IDs so it matches the confirmed draft. Within-flow memory is LangGraph
interrupt-replay — this node is re-entrant and holds no persistent state."""


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
