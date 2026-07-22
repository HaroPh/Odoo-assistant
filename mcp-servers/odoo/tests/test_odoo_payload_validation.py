"""Integration tests for sanitize_payload_keys wired into odoo(). Unlike
most files in this dir, these call the REAL server.odoo() (not a fake) —
only the XML-RPC transport is mocked, so the actual gate chain (sanitize_
model -> sanitize_payload_keys -> classify_operation -> rate_limit ->
execute_kw) runs for real."""
import xmlrpc.client

import pytest

import server


class _FakeProxy:
    def __init__(self, execute_kw_return=True, calls=None):
        self._execute_kw_return = execute_kw_return
        self._calls = calls  # list to append (model, method, args, kwargs) to, or None

    def execute_kw(self, db, uid, pwd, model, method, args, kwargs):
        if self._calls is not None:
            self._calls.append((model, method, args, kwargs))
        return self._execute_kw_return


def _patch_transport(monkeypatch, execute_kw_return=True, calls=None):
    """calls: pass a list to record every execute_kw invocation into it (used
    by the reject tests to prove execute_kw was never reached)."""
    monkeypatch.setattr(server, "_uid", 1)  # skip real authentication
    monkeypatch.setattr(xmlrpc.client, "ServerProxy",
                        lambda url: _FakeProxy(execute_kw_return, calls))


def test_odoo_allows_valid_payload_through_to_execute_kw(monkeypatch):
    _patch_transport(monkeypatch, execute_kw_return=[{"id": 1, "name": "ok"}])
    result = server.odoo("res.partner", "search_read",
                         [[["id", "=", 1]]], {"fields": ["id", "name"]},
                         tool_name="test_valid_payload")
    assert result == [{"id": 1, "name": "ok"}]


def test_odoo_rejects_bad_key_before_reaching_execute_kw(monkeypatch):
    calls = []
    _patch_transport(monkeypatch, calls=calls)

    with pytest.raises(ValueError):
        server.odoo("res.partner", "search_read",
                    [[["id", "=", 1]]], {"totally bad key!!": 1},
                    tool_name="test_invalid_payload")
    assert calls == []  # execute_kw never reached


def test_odoo_rejects_bad_key_in_args_vals_dict(monkeypatch):
    # Mirrors register_payment's actual call shape: vals dict lives in
    # args[0] for create, not in kwargs.
    calls = []
    _patch_transport(monkeypatch, calls=calls)

    with pytest.raises(ValueError):
        server.odoo("account.payment.register", "create",
                    [{"journal_id": 3, "bad field!": 1}], {"context": {}},
                    tool_name="test_invalid_args_payload")
    assert calls == []
