import event_log


def test_truncate_leaves_short_text_untouched():
    assert event_log._truncate("short") == "short"
    assert event_log._truncate(None) is None


def test_truncate_cuts_long_text():
    long_text = "x" * (event_log.MAX_TEXT + 100)
    out = event_log._truncate(long_text)
    assert len(out) == event_log.MAX_TEXT + len("... [truncated]")
    assert out.endswith("... [truncated]")


def test_get_db_returns_none_without_database_url(monkeypatch):
    monkeypatch.setattr(event_log, "DATABASE_URL", None)
    monkeypatch.setattr(event_log, "_db_conn", None)
    assert event_log._get_db() is None


def test_log_mcp_event_never_raises_when_db_unavailable(monkeypatch):
    monkeypatch.setattr(event_log, "DATABASE_URL", None)
    monkeypatch.setattr(event_log, "_db_conn", None)
    # Fail-open theo thiết kế gốc — log lỗi DB không được làm hỏng tool.
    event_log.log_mcp_event("model_access", tool_name="x", model_name="y",
                            operation="read", duration_ms=5)


import audit_chain


class _FakeCursor:
    """Mô phỏng psycopg2 cursor: hỗ trợ context manager, ghi lại mọi
    execute() để test kiểm tra, fetchone() trả về hàng đã cấu hình sẵn
    (mô phỏng dòng cuối cùng trong mcp_call_log, hoặc None nếu bảng rỗng)."""
    def __init__(self, last_hash_row=None):
        self.queries = []  # list of (sql, params)
        self._last_hash_row = last_hash_row

    def execute(self, sql, params=None):
        self.queries.append((sql, params))

    def fetchone(self):
        return self._last_hash_row

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, last_hash_row=None):
        self.cursor_obj = _FakeCursor(last_hash_row)
        self.closed = 0

    def cursor(self):
        return self.cursor_obj


def test_log_mcp_event_first_row_uses_genesis_hash(monkeypatch):
    fake_conn = _FakeConn(last_hash_row=None)
    monkeypatch.setattr(event_log, "_get_db", lambda: fake_conn)

    event_log.log_mcp_event("model_access", tool_name="confirm_sale_order",
                            model_name="sale.order", operation="write",
                            duration_ms=10)

    insert_sql, insert_params = fake_conn.cursor_obj.queries[-1]
    assert "INSERT INTO mcp_call_log" in insert_sql
    created_at, entry_hash, prev_hash = insert_params[-3:]
    assert prev_hash == audit_chain.GENESIS_HASH
    expected = audit_chain.compute_entry_hash(
        audit_chain.GENESIS_HASH, created_at, "model_access", "mcp-odoo",
        "confirm_sale_order", "sale.order", "write", 10, None, None)
    assert entry_hash == expected


def test_log_mcp_event_chains_to_previous_hash(monkeypatch):
    fake_conn = _FakeConn(last_hash_row=("a" * 64,))
    monkeypatch.setattr(event_log, "_get_db", lambda: fake_conn)

    event_log.log_mcp_event("error", error_code="E500", error_message="boom")

    _, insert_params = fake_conn.cursor_obj.queries[-1]
    prev_hash = insert_params[-1]
    assert prev_hash == "a" * 64
