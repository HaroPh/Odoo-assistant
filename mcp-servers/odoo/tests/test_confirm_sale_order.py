import server


def test_action_confirm_is_whitelisted_as_write():
    assert server.classify_operation("action_confirm") == "write"


def test_action_confirm_blocked_when_write_disabled(monkeypatch):
    # Gateway must refuse a write op while the flag is off.
    monkeypatch.setattr(server, "WRITE_ENABLED", False)
    import pytest
    with pytest.raises(ValueError):
        server.odoo("sale.order", "action_confirm", [[1]])
