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
