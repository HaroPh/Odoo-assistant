# backend/tests/jobs/test_run_eval_errors.py
"""S2: lỗi thoáng qua không phá set; lỗi dai dẳng vào errors → exit 2, cấm baseline."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.evals import run_eval
from backend.evals.cases import INTENT_CASES

_INTENT_TEXTS = [t for t, _ in INTENT_CASES]


class _ScriptedLLM:
    """Fake LLM: raise theo kịch bản per-case, sau đó trả reply cố định.

    fail_plan: {case_text: số lần raise trước khi trả lời}
    """
    def __init__(self, reply="erp_read", fail_plan=None):
        self._reply = reply
        self._fail_plan = dict(fail_plan or {})
        self.calls_per_case = {}

    async def ainvoke(self, messages):
        text = messages[-1].content
        n = self.calls_per_case.get(text, 0) + 1
        self.calls_per_case[text] = n
        if n <= self._fail_plan.get(text, 0):
            raise ConnectionError(f"blip {n}")
        reply = self._reply
        class R:
            content = reply
        return R()


def _silence_sleep(monkeypatch):
    async def fake_sleep(s):
        pass
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)


async def test_transient_error_does_not_destroy_set(monkeypatch):
    _silence_sleep(monkeypatch)
    # case đầu tiên lỗi 1 lần rồi trả lời được → retry cứu, KHÔNG có error
    llm = _ScriptedLLM(fail_plan={_INTENT_TEXTS[0]: 1})
    result = await run_eval.eval_intent(llm)
    assert result["errors"] == []
    assert result["n"] == len(INTENT_CASES)
    # case đó được gọi đúng 2 lần (1 lỗi + 1 thành công)
    assert llm.calls_per_case[_INTENT_TEXTS[0]] == 2


async def test_persistent_error_recorded_others_still_measured(monkeypatch):
    _silence_sleep(monkeypatch)
    # case đầu chết hẳn (raise nhiều hơn tổng attempt=3) → vào errors;
    # các case khác vẫn được đo bình thường
    llm = _ScriptedLLM(fail_plan={_INTENT_TEXTS[0]: 99})
    result = await run_eval.eval_intent(llm)
    assert len(result["errors"]) == 1
    err = result["errors"][0]
    assert err["attempts"] == 3 and "blip" in err["error"]
    assert llm.calls_per_case[_INTENT_TEXTS[0]] == 3   # bounded, không retry vô hạn
    # errors không lẫn vào fails
    assert all(f.get("got") for f in result["fails"])


async def test_confirm_api_error_never_counts_as_false_confirm(monkeypatch):
    _silence_sleep(monkeypatch)
    from backend.evals.cases import CONFIRM_CASES
    text0 = CONFIRM_CASES[0][0]
    llm = _ScriptedLLM(reply="CANCEL", fail_plan={text0: 99})
    result = await run_eval.eval_confirm(llm)
    assert len(result["errors"]) == 1
    # lỗi API không bao giờ được đếm là false_confirm (hướng nguy hiểm)
    assert result["false_confirm"] == sum(
        1 for f in result["fails"] if f["got"] == "confirm")


async def test_main_exits_2_on_errors_and_never_saves_baseline(monkeypatch, tmp_path):
    async def fake_eval(llm, pace=0.0, checkpoint_path=None):
        return {"set": "intent", "n": 40, "acc": 0.975, "fails": [],
                "errors": [{"item": ["x", "y"], "error": "chết", "attempts": 3}]}
    monkeypatch.setattr(run_eval, "eval_intent", fake_eval)
    monkeypatch.setattr(run_eval, "_llm", lambda m: object())
    with pytest.raises(SystemExit) as ei:
        await run_eval.main(["--set", "intent", "--model", "fake-model-x",
                             "--save-baseline"])
    assert ei.value.code == 2
    # baseline KHÔNG được ghi (file đặt cạnh run_eval.py theo tên model)
    evals_dir = os.path.dirname(run_eval.__file__)
    assert not os.path.exists(
        os.path.join(evals_dir, "baseline-fake-model-x-intent.json"))
