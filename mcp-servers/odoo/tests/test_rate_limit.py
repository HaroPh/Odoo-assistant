import rate_limit


def test_allows_calls_under_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "RATE_LIMIT", 3)
    rate_limit._rate_cache.clear()
    caller = "test-under"
    assert rate_limit.check_rate_limit(caller) is True
    assert rate_limit.check_rate_limit(caller) is True
    assert rate_limit.check_rate_limit(caller) is True


def test_blocks_calls_over_limit(monkeypatch):
    monkeypatch.setattr(rate_limit, "RATE_LIMIT", 2)
    rate_limit._rate_cache.clear()
    caller = "test-over"
    assert rate_limit.check_rate_limit(caller) is True
    assert rate_limit.check_rate_limit(caller) is True
    assert rate_limit.check_rate_limit(caller) is False


def test_callers_are_independent(monkeypatch):
    monkeypatch.setattr(rate_limit, "RATE_LIMIT", 1)
    rate_limit._rate_cache.clear()
    assert rate_limit.check_rate_limit("caller-a") is True
    assert rate_limit.check_rate_limit("caller-b") is True   # bucket riêng
    assert rate_limit.check_rate_limit("caller-a") is False  # caller-a đã hết
