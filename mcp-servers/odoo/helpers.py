"""Generic response/formatting helpers dùng chung bởi các MCP tool (spec
2026-07-13-mcp-server-modularization)."""
import json
from datetime import datetime, timezone


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def resolve_unique(rows, kind_label, describe, hint=""):
    """Pick a single record from candidate rows, or describe the choices.

    Returns (row, None) when exactly one candidate matches; (None, message)
    when none match or several do. `describe(row)` returns a short
    distinguishing string used in the multi-candidate listing. Reusable by
    any resolution tool.
    """
    if not rows:
        return None, f"Không tìm thấy {kind_label} nào phù hợp."
    if len(rows) == 1:
        return rows[0], None
    listing = "\n".join(f"  • {describe(r)}" for r in rows)
    msg = f"Có nhiều {kind_label}:\n{listing}"
    if hint:
        msg += f"\n{hint}"
    return None, msg

def envelope(ok: bool, display: str, *, ref=None, model=None,
             res_id=None, state=None) -> str:
    """JSON-string result for the invoice-chain tools (create_quotation,
    confirm_sale_order, create_invoice_from_order, post_invoice). The backend
    parses this to drive the write-continuation menu; `display` is the
    user-facing Vietnamese sentence. Non-chain tools keep plain strings."""
    return json.dumps({"ok": ok, "ref": ref, "model": model, "res_id": res_id,
                       "state": state, "display": display}, ensure_ascii=False)
