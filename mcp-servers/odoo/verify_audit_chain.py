"""Kiểm tra tính toàn vẹn chuỗi hash trong mcp_call_log — chạy tay khi cần
điều tra: `python verify_audit_chain.py`. Xem docs/superpowers/specs/
2026-07-23-audit-trail-hash-chain-design.md."""
import psycopg2

import audit_chain
from config import DATABASE_URL

_COLUMNS = ["id", "entry_hash", "prev_hash", "created_at", "event_type",
           "caller", "tool_name", "model_name", "operation", "duration_ms",
           "error_code", "error_message"]


def fetch_rows(conn) -> list[dict]:
    """Đọc mọi dòng đã hash-chain (entry_hash khác NULL) theo thứ tự id."""
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT {", ".join(_COLUMNS)}
            FROM mcp_call_log
            WHERE entry_hash IS NOT NULL
            ORDER BY id
        """)
        return [dict(zip(_COLUMNS, row)) for row in cur.fetchall()]


def verify(rows: list[dict]) -> tuple[bool, str]:
    """Duyệt các dòng đã hash-chain theo thứ tự id (rows PHẢI đã ORDER BY
    id), tính lại từng hash và so khớp entry_hash + liên kết prev_hash với
    dòng ngay trước."""
    prev = audit_chain.GENESIS_HASH
    for row in rows:
        if row["prev_hash"] != prev:
            return False, f"Chuỗi đứt tại id={row['id']}: prev_hash không khớp"
        expected = audit_chain.compute_entry_hash(
            prev, row["created_at"], row["event_type"], row["caller"],
            row["tool_name"], row["model_name"], row["operation"],
            row["duration_ms"], row["error_code"], row["error_message"])
        if expected != row["entry_hash"]:
            return False, f"Chuỗi đứt tại id={row['id']}: entry_hash không khớp"
        prev = row["entry_hash"]
    return True, f"OK — {len(rows)} dòng, chuỗi nguyên vẹn"


def main() -> None:
    conn = psycopg2.connect(DATABASE_URL)
    try:
        rows = fetch_rows(conn)
    finally:
        conn.close()
    ok, msg = verify(rows)
    print(msg)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
