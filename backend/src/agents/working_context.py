# backend/src/agents/working_context.py
"""Cross-turn working context — bộ nhớ CÓ CẤU TRÚC per-thread về bản ghi ĐƠN
đang làm việc. Leaf module (stdlib only). Cả hai hàm là TOTAL FUNCTIONS:
không bao giờ raise, không I/O — chúng nằm trên đường trả về của executor,
một exception ở đây phá hỏng một write đã thành công phía Odoo (invariant B)."""
import re

# model kỹ thuật -> nhãn tiếng Việt (dùng cho render); đồng thời là allowlist
# các model được nhớ (chỉ ĐƠN — không nhớ hoá đơn account.move, giữ nguyên
# quyết định không dạy LLM đoán invoice_id).
ORDER_MODELS = {"sale.order": "đơn bán", "purchase.order": "đơn mua"}

# Mã đơn tường minh trong lời user: S00040, P00015… (case-sensitive — mã Odoo
# luôn viết hoa; tránh khớp nhầm chữ thường trong từ tiếng Việt).
_REF_RE = re.compile(r"\b[SP]\d{3,}\b")


def derive_working_context(env):
    """Envelope của một write thành công -> context dict | None (total).

    Chỉ trả dict khi ok=true VÀ có ref VÀ model là đơn (ORDER_MODELS).
    Bước hoá đơn/bill (ref=None / account.move) và envelope lỗi -> None."""
    try:
        if not isinstance(env, dict) or not env.get("ok"):
            return None
        ref, model = env.get("ref"), env.get("model")
        if not ref or model not in ORDER_MODELS:
            return None
        return {"ref": ref, "model": model,
                "display": str(env.get("display") or "")}
    except Exception:
        return None


def enforce_explicit_ref(plan, user_text):
    """Invariant C tầng code: mã TƯỜNG MINH trong lời user thắng context.

    Nếu user_text chứa đúng MỘT mã duy nhất dạng S/P+số và plan có arg
    `order_ref` khác mã đó -> trả plan MỚI với order_ref = mã tường minh.
    Mọi trường hợp khác (0 mã, >=2 mã khác nhau, plan không có order_ref,
    input dị dạng) -> trả plan nguyên vẹn. Total: không raise, không mutate."""
    try:
        if not isinstance(plan, dict):
            return plan
        args = plan.get("args")
        if not isinstance(args, dict) or "order_ref" not in args:
            return plan
        refs = set(_REF_RE.findall(user_text or ""))
        if len(refs) != 1:
            return plan
        explicit = next(iter(refs))
        if args.get("order_ref") == explicit:
            return plan
        return {**plan, "args": {**args, "order_ref": explicit}}
    except Exception:
        return plan
