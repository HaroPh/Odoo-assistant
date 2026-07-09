# backend/tests/jobs/test_pacing.py
"""R8: pacing giữa các call eval — cloud RPM=15 cần giãn cách, local thì không."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.evals import run_eval
from backend.evals.cases import CONFIRM_CASES, INTENT_CASES


class _FakeLLM:
    def __init__(self, reply):
        self._reply = reply

    async def ainvoke(self, messages):
        reply = self._reply
        class R:
            content = reply
        return R()


async def test_intent_pace_sleeps_between_calls(monkeypatch):
    sleeps = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await run_eval.eval_intent(_FakeLLM("erp_read"), pace=5.0)
    assert len(sleeps) == len(INTENT_CASES) - 1     # giữa các call, không đầu/cuối
    assert all(s == 5.0 for s in sleeps)


async def test_confirm_pace_sleeps_between_calls(monkeypatch):
    sleeps = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await run_eval.eval_confirm(_FakeLLM("CONFIRM"), pace=2.0)
    assert len(sleeps) == len(CONFIRM_CASES) - 1


async def test_pace_zero_never_sleeps(monkeypatch):
    sleeps = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await run_eval.eval_intent(_FakeLLM("erp_read"), pace=0.0)
    assert sleeps == []


async def test_default_pace_is_zero_backward_compat(monkeypatch):
    sleeps = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await run_eval.eval_intent(_FakeLLM("erp_read"))    # không truyền pace
    assert sleeps == []
