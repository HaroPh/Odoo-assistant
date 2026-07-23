from datetime import datetime, timezone

import audit_chain
import verify_audit_chain


_BASE_KWARGS = dict(
    event_type="model_access", caller="mcp-odoo", tool_name="confirm_sale_order",
    model_name="sale.order", operation="write", duration_ms=12,
    error_code=None, error_message=None,
)


def _build_valid_chain(n: int) -> list[dict]:
    rows = []
    prev = audit_chain.GENESIS_HASH
    for i in range(1, n + 1):
        ts = datetime(2026, 7, 23, 10, 0, i, tzinfo=timezone.utc)
        entry_hash = audit_chain.compute_entry_hash(prev, ts, **_BASE_KWARGS)
        row = dict(_BASE_KWARGS, id=i, created_at=ts, entry_hash=entry_hash,
                  prev_hash=prev)
        rows.append(row)
        prev = entry_hash
    return rows


def test_verify_reports_ok_for_valid_chain():
    rows = _build_valid_chain(3)
    ok, msg = verify_audit_chain.verify(rows)
    assert ok is True
    assert "3" in msg


def test_verify_detects_tampered_middle_row():
    rows = _build_valid_chain(3)
    rows[1]["operation"] = "unlink"  # sửa dữ liệu dòng giữa, KHÔNG tính lại hash

    ok, msg = verify_audit_chain.verify(rows)

    assert ok is False
    assert "id=2" in msg


def test_verify_empty_chain_is_ok():
    ok, msg = verify_audit_chain.verify([])
    assert ok is True
    assert "0" in msg


class _FakeCursor:
    def __init__(self, rows=None):
        self.queries = []
        self._rows = rows or []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, rows=None):
        self.cursor_obj = _FakeCursor(rows)

    def cursor(self):
        return self.cursor_obj


def test_fetch_rows_maps_columns_correctly():
    ts = datetime(2026, 7, 23, 10, 0, 0, tzinfo=timezone.utc)
    raw_row = (1, "hash1", audit_chain.GENESIS_HASH, ts, "model_access",
              "mcp-odoo", "confirm_sale_order", "sale.order", "write", 12,
              None, None)
    fake_conn = _FakeConn(rows=[raw_row])

    rows = verify_audit_chain.fetch_rows(fake_conn)

    assert rows == [{
        "id": 1, "entry_hash": "hash1", "prev_hash": audit_chain.GENESIS_HASH,
        "created_at": ts, "event_type": "model_access", "caller": "mcp-odoo",
        "tool_name": "confirm_sale_order", "model_name": "sale.order",
        "operation": "write", "duration_ms": 12, "error_code": None,
        "error_message": None,
    }]
