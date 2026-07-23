from datetime import datetime, timedelta, timezone

import audit_chain


_BASE_KWARGS = dict(
    event_type="model_access", caller="mcp-odoo", tool_name="confirm_sale_order",
    model_name="sale.order", operation="write", duration_ms=12,
    error_code=None, error_message=None,
)


def test_compute_entry_hash_is_deterministic():
    ts = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
    h1 = audit_chain.compute_entry_hash(audit_chain.GENESIS_HASH, ts, **_BASE_KWARGS)
    h2 = audit_chain.compute_entry_hash(audit_chain.GENESIS_HASH, ts, **_BASE_KWARGS)
    assert h1 == h2
    assert len(h1) == 64


def test_compute_entry_hash_changes_with_prev_hash():
    ts = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
    h1 = audit_chain.compute_entry_hash(audit_chain.GENESIS_HASH, ts, **_BASE_KWARGS)
    h2 = audit_chain.compute_entry_hash("a" * 64, ts, **_BASE_KWARGS)
    assert h1 != h2


def test_compute_entry_hash_changes_with_any_field():
    ts = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
    h1 = audit_chain.compute_entry_hash(audit_chain.GENESIS_HASH, ts, **_BASE_KWARGS)
    tampered = dict(_BASE_KWARGS, operation="unlink")
    h2 = audit_chain.compute_entry_hash(audit_chain.GENESIS_HASH, ts, **tampered)
    assert h1 != h2


def test_compute_entry_hash_normalizes_timezone_before_hashing():
    """Cùng 1 thời điểm tuyệt đối nhưng 2 tzinfo khác nhau (mô phỏng
    psycopg2 trả về offset khác lúc đọc lại so với lúc ghi) phải cho ra
    CÙNG 1 hash — nếu không, verify sẽ luôn báo sai dù không ai giả mạo
    gì. isoformat() của 2 giá trị này KHÁC NHAU dù cùng 1 thời điểm — đó
    chính là lỗi cần chặn."""
    utc_ts = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
    plus7_ts = utc_ts.astimezone(timezone(timedelta(hours=7)))
    assert utc_ts.isoformat() != plus7_ts.isoformat()  # xác nhận tiền đề bug

    h1 = audit_chain.compute_entry_hash(audit_chain.GENESIS_HASH, utc_ts, **_BASE_KWARGS)
    h2 = audit_chain.compute_entry_hash(audit_chain.GENESIS_HASH, plus7_ts, **_BASE_KWARGS)
    assert h1 == h2
