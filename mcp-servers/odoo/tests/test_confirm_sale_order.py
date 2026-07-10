import server


def test_action_confirm_is_whitelisted_as_write():
    assert server.classify_operation("action_confirm") == "write"


def test_action_confirm_blocked_when_write_disabled(monkeypatch):
    # Gateway must refuse a write op while the flag is off.
    monkeypatch.setattr(server, "write_actions_enabled", lambda: False)
    import pytest
    with pytest.raises(ValueError):
        server.odoo("sale.order", "action_confirm", [[1]])


import pytest


def _call(monkeypatch, rows, confirm_capture=None):
    """Patch server.odoo to return `rows` for search_read and capture confirm."""
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append((model, method, args, kwargs))
        if method == "search_read":
            return rows
        if method == "action_confirm":
            if confirm_capture is not None:
                confirm_capture.append((model, args))
            return True
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


def _fn():
    # Raw function behind the @mcp.tool() wrapper.
    return getattr(server.confirm_sale_order, "fn", server.confirm_sale_order)


def test_confirm_not_found(monkeypatch):
    _call(monkeypatch, rows=[])
    out = _fn()("S99999")
    assert "không tìm thấy" in out.lower()


def test_confirm_ambiguous(monkeypatch):
    _call(monkeypatch, rows=[{"id": 1, "name": "S001", "state": "draft"},
                             {"id": 2, "name": "S001", "state": "draft"}])
    out = _fn()("S001")
    assert "nhiều" in out.lower()


def test_confirm_already_confirmed_is_idempotent(monkeypatch):
    confirms = []
    _call(monkeypatch, rows=[{"id": 5, "name": "S00012", "state": "sale"}],
          confirm_capture=confirms)
    out = _fn()("S00012")
    assert "đã được xác nhận" in out.lower()
    assert confirms == []  # action_confirm NOT called


def test_confirm_cancelled_refused(monkeypatch):
    confirms = []
    _call(monkeypatch, rows=[{"id": 6, "name": "S00013", "state": "cancel"}],
          confirm_capture=confirms)
    out = _fn()("S00013")
    assert "hủy" in out.lower()
    assert confirms == []


def test_confirm_draft_calls_action_confirm(monkeypatch):
    confirms = []
    _call(monkeypatch, rows=[{"id": 7, "name": "S00014", "state": "draft"}],
          confirm_capture=confirms)
    out = _fn()("S00014")
    assert "đã xác nhận" in out.lower()
    assert confirms == [("sale.order", [[7]])]  # called once with the id


def test_write_gate_fail_closed_when_odoo_unreachable(monkeypatch):
    # cache module-level persist giữa các test — reset trước
    server._write_gate_cache["expires_at"] = 0.0
    def boom(*a, **k):
        raise ConnectionError("odoo down")
    monkeypatch.setattr(server, "odoo", boom)
    assert server.write_actions_enabled() is False


def test_write_gate_caches_within_ttl(monkeypatch):
    server._write_gate_cache["expires_at"] = 0.0
    calls = {"n": 0}
    def fake_odoo(*a, **k):
        calls["n"] += 1
        return [{"id": 1, "value": "true"}]
    monkeypatch.setattr(server, "odoo", fake_odoo)
    assert server.write_actions_enabled() is True
    assert server.write_actions_enabled() is True
    assert calls["n"] == 1
