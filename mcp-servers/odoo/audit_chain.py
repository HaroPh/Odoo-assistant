"""Tính hash-chain (sha256 nối tiếp) cho mỗi dòng mcp_call_log — dùng chung
cho phía ghi (event_log.py) và phía kiểm tra (verify_audit_chain.py) để
công thức hash không bao giờ lệch nhau giữa 2 nơi. Xem docs/superpowers/
specs/2026-07-23-audit-trail-hash-chain-design.md."""
import hashlib
from datetime import timezone

GENESIS_HASH = "0" * 64


def compute_entry_hash(prev_hash, created_at, event_type, caller, tool_name,
                       model_name, operation, duration_ms, error_code,
                       error_message) -> str:
    """sha256 nối chuỗi prev_hash + các field của 1 dòng mcp_call_log.
    created_at LUÔN chuẩn hoá về UTC trước khi format — nếu không, giá trị
    đọc lại từ Postgres (TIMESTAMPTZ, có thể trả tzinfo khác lúc ghi dù
    CÙNG một thời điểm) sẽ cho ra chuỗi isoformat khác, khiến verify luôn
    báo sai dù không ai giả mạo gì."""
    ts = created_at.astimezone(timezone.utc).isoformat()
    chain_data = ":".join(str(x) for x in (
        prev_hash, ts, event_type, caller, tool_name, model_name, operation,
        duration_ms, error_code, error_message))
    return hashlib.sha256(chain_data.encode("utf-8")).hexdigest()
