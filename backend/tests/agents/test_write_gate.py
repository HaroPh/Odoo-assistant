# backend/tests/agents/test_write_gate.py
"""S3 write toggle: đọc ir.config_parameter qua transport riêng, cache TTL,
fail-closed trên MỌI đường không xác minh được value == "true"."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents import write_gate


class _FakeTransport:
    def __init__(self, rows=None, exc=None):
        self.rows = rows if rows is not None else []
        self.exc = exc
        self.calls = 0

    def call(self, model, method, args, kwargs):
        self.calls += 1
        assert model == "ir.config_parameter" and method == "search_read"
        if self.exc:
            raise self.exc
        return self.rows


@pytest.fixture(autouse=True)
def _fresh_cache():
    # cache module-level persist giữa các test — reset trước mỗi test
    write_gate._cache["expires_at"] = 0.0
    write_gate._cache["value"] = False
    yield


def _use(monkeypatch, transport):
    monkeypatch.setattr(write_gate, "_get_transport", lambda: transport)


def test_true_value_enables(monkeypatch):
    _use(monkeypatch, _FakeTransport(rows=[{"id": 1, "value": "true"}]))
    assert write_gate.write_actions_enabled() is True


def test_cache_hit_within_ttl_single_transport_call(monkeypatch):
    t = _FakeTransport(rows=[{"id": 1, "value": "true"}])
    _use(monkeypatch, t)
    assert write_gate.write_actions_enabled() is True
    assert write_gate.write_actions_enabled() is True
    assert t.calls == 1


def test_transport_exception_fails_closed(monkeypatch):
    _use(monkeypatch, _FakeTransport(exc=ConnectionError("odoo sập")))
    assert write_gate.write_actions_enabled() is False


def test_missing_key_fails_closed(monkeypatch):
    # search_read trả [] = key chưa được tạo trong Odoo
    _use(monkeypatch, _FakeTransport(rows=[]))
    assert write_gate.write_actions_enabled() is False


@pytest.mark.parametrize("value,expected", [
    ("false", False), ("", False), ("1", False), ("yes", False),
    ("TRUE ", True), (" True", True),
    (False, False),   # Odoo XML-RPC trả False (không phải None) cho char rỗng
])
def test_value_normalization(monkeypatch, value, expected):
    _use(monkeypatch, _FakeTransport(rows=[{"id": 1, "value": value}]))
    assert write_gate.write_actions_enabled() is expected


def test_cache_expiry_rereads(monkeypatch):
    t = _FakeTransport(rows=[{"id": 1, "value": "true"}])
    _use(monkeypatch, t)
    fake_now = [100.0]
    monkeypatch.setattr(write_gate.time, "monotonic", lambda: fake_now[0])
    assert write_gate.write_actions_enabled() is True
    fake_now[0] += write_gate._CACHE_TTL_S + 0.1
    assert write_gate.write_actions_enabled() is True
    assert t.calls == 2


def test_error_result_cached_no_retry_within_ttl(monkeypatch):
    t = _FakeTransport(exc=ConnectionError("odoo sập"))
    _use(monkeypatch, t)
    assert write_gate.write_actions_enabled() is False
    assert write_gate.write_actions_enabled() is False
    assert t.calls == 1   # kết quả lỗi cũng cache — không spam retry
