# backend/tests/jobs/test_resilience.py
"""S2 resilience helper: bounded retry, checkpoint làm bằng chứng, circuit-breaker."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.jobs.resilience import CircuitBreakerOpen, run_resilient


def _flaky(fail_times):
    """call_fn lỗi `fail_times` lần đầu cho MỖI item rồi thành công (pass)."""
    counts = {}
    async def call(item):
        counts[item] = counts.get(item, 0) + 1
        if counts[item] <= fail_times:
            raise ConnectionError(f"blip {item}")
        return None
    return call


async def test_retry_recovers_transient_failure():
    # lỗi 2 lần + thành công lần 3 = vừa khít max_retries=2 (3 attempt)
    fails, errors = await run_resilient([1, 2, 3], _flaky(2), retry_delay=0)
    assert fails == [] and errors == []


async def test_fail_record_goes_to_fails_not_errors():
    async def call(item):
        return {"item": item, "got": "sai"} if item == 2 else None
    fails, errors = await run_resilient([1, 2, 3], call)
    assert fails == [{"item": 2, "got": "sai"}] and errors == []


async def test_exhausted_retries_goes_to_errors_not_fails():
    async def call(item):
        if item == 2:
            raise TimeoutError("chết hẳn")
        return None
    fails, errors = await run_resilient([1, 2, 3], call, retry_delay=0)
    assert fails == []
    assert errors == [{"item": 2, "error": "chết hẳn", "attempts": 3}]


async def test_breaker_trips_after_5_consecutive_and_keeps_checkpoint(tmp_path):
    cp = tmp_path / "cp.json"
    async def call(item):
        raise ConnectionError("key chết")
    with pytest.raises(CircuitBreakerOpen):
        await run_resilient(list(range(10)), call, retry_delay=0,
                            checkpoint_path=cp)
    # bằng chứng chết giữa chừng GIỮ LẠI, và ghi đúng trạng thái lúc trip
    state = json.loads(cp.read_text(encoding="utf-8"))
    assert state["done"] == 5 and state["errors"] == 5 and state["total"] == 10


async def test_success_resets_breaker_counter():
    # 4 lỗi, 1 pass, 4 lỗi → không bao giờ đủ 5 LIÊN TIẾP → không trip
    async def call(item):
        if item == "ok":
            return None
        raise ConnectionError("lỗi")
    items = ["e1", "e2", "e3", "e4", "ok", "e5", "e6", "e7", "e8"]
    fails, errors = await run_resilient(items, call, retry_delay=0)
    assert len(errors) == 8 and fails == []


async def test_fail_record_also_resets_breaker_counter():
    # đo được (model trả lời SAI) vẫn là hạ tầng sống → reset counter
    async def call(item):
        if item == "bad-answer":
            return {"item": item, "got": "sai"}
        raise ConnectionError("lỗi")
    items = ["e1", "e2", "e3", "e4", "bad-answer", "e5", "e6", "e7", "e8"]
    fails, errors = await run_resilient(items, call, retry_delay=0)
    assert len(fails) == 1 and len(errors) == 8


async def test_checkpoint_written_every_10_and_deleted_on_clean_finish(tmp_path):
    cp = tmp_path / "cp.json"
    seen = []
    async def call(item):
        if item == 10:   # item thứ 11 (0-based): checkpoint 10 item đầu đã ghi
            seen.append(json.loads(cp.read_text(encoding="utf-8")))
        return None
    await run_resilient(list(range(25)), call, checkpoint_path=cp)
    assert seen[0]["done"] == 10 and seen[0]["total"] == 25
    assert seen[0]["fails"] == 0 and seen[0]["errors"] == 0
    assert not cp.exists()   # run sạch: xóa bằng chứng


async def test_checkpoint_path_none_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    async def call(item):
        return None
    await run_resilient(list(range(12)), call, checkpoint_path=None)
    assert list(tmp_path.iterdir()) == []


async def test_pace_sleeps_between_items_not_before_first(monkeypatch):
    import asyncio
    sleeps = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    async def call(item):
        return None
    await run_resilient([1, 2, 3], call, pace=5.0)
    assert sleeps == [5.0, 5.0]   # giữa các item, không trước item đầu


async def test_retry_delay_defaults_to_pace_when_pacing(monkeypatch):
    import asyncio
    sleeps = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    fails, errors = await run_resilient([1], _flaky(1), pace=5.0)
    # 1 item duy nhất: không pace-sleep; 1 retry-sleep = pace (R8 cloud RPM)
    assert sleeps == [5.0] and errors == []
