# backend/tests/jobs/test_eval_gate.py
"""eval-gate: verdict aggregation, resolution model theo config sống, pacing auto."""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.evals import run_eval
from backend.jobs import eval_gate
from backend.jobs.registry import GATE_FAIL, INFRA_ERROR, PASS


def _args(model=None, set_="both", pace=None):
    return argparse.Namespace(model=model, set=set_, pace=pace)


def _fake_eval(set_name, acc, false_confirm=0, n=40):
    async def fn(llm, pace=0.0):
        fn.calls.append({"pace": pace})
        d = {"set": set_name, "n": n, "acc": acc, "fails": []}
        if set_name == "confirm":
            d["false_confirm"] = false_confirm
        return d
    fn.calls = []
    return fn


def _patch(monkeypatch, intent_acc=1.0, confirm_acc=1.0, false_confirm=0):
    fi = _fake_eval("intent", intent_acc)
    fc = _fake_eval("confirm", confirm_acc, false_confirm, n=24)
    monkeypatch.setitem(eval_gate.EVAL_FN, "intent", fi)
    monkeypatch.setitem(eval_gate.EVAL_FN, "confirm", fc)
    monkeypatch.setattr(run_eval, "_llm", lambda m: object())
    return fi, fc


def test_both_pass_exit_zero(monkeypatch):
    _patch(monkeypatch)
    result = eval_gate.run(_args())
    assert result.exit_code == PASS and result.verdict == "PASS"
    assert set(result.detail) == {"intent", "confirm"}


def test_one_set_fail_exit_one(monkeypatch):
    # confirm: false_confirm=1 → fail bất kể acc (điều kiện tuyệt đối M3)
    _patch(monkeypatch, confirm_acc=1.0, false_confirm=1)
    result = eval_gate.run(_args())
    assert result.exit_code == GATE_FAIL and result.verdict == "FAIL"
    assert result.detail["confirm"]["gate"] == "FAIL"
    assert result.detail["intent"]["gate"] == "PASS"


def test_intent_below_baseline_fails(monkeypatch):
    _patch(monkeypatch, intent_acc=0.0)
    result = eval_gate.run(_args(set_="intent"))
    assert result.exit_code == GATE_FAIL


def test_eval_exception_exit_two(monkeypatch):
    async def boom(llm, pace=0.0):
        raise ConnectionError("litellm chết")
    monkeypatch.setitem(eval_gate.EVAL_FN, "intent", boom)
    monkeypatch.setattr(run_eval, "_llm", lambda m: object())
    result = eval_gate.run(_args(set_="intent"))
    assert result.exit_code == INFRA_ERROR and result.verdict == "ERROR"
    assert "litellm chết" in result.detail["intent"]["error"]


def test_set_intent_only_runs_one_set(monkeypatch):
    fi, fc = _patch(monkeypatch)
    result = eval_gate.run(_args(set_="intent"))
    assert list(result.detail) == ["intent"]
    assert len(fi.calls) == 1 and len(fc.calls) == 0


def test_default_measures_live_config(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setenv("MODEL_ROUTER", "gemini-flash-lite")
    monkeypatch.delenv("MODEL_EVALUATOR", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)   # tránh flaky theo env máy dev
    result = eval_gate.run(_args())
    assert result.detail["intent"]["model"] == "gemini-flash-lite"
    assert result.detail["confirm"]["model"] == "qwen3:8b"   # default local


def test_model_override_applies_to_all_sets(monkeypatch):
    _patch(monkeypatch)
    result = eval_gate.run(_args(model="candidate-x"))
    assert result.detail["intent"]["model"] == "candidate-x"
    assert result.detail["confirm"]["model"] == "candidate-x"


def test_pace_auto_cloud_5s_local_0(monkeypatch):
    fi, fc = _patch(monkeypatch)
    monkeypatch.setenv("MODEL_ROUTER", "gemini-flash-lite")   # cloud
    monkeypatch.delenv("MODEL_EVALUATOR", raising=False)      # local qwen
    monkeypatch.delenv("AGENT_MODEL", raising=False)          # tránh flaky theo env
    eval_gate.run(_args())
    assert fi.calls[0]["pace"] == eval_gate.CLOUD_PACE_S       # 5.0
    assert fc.calls[0]["pace"] == 0.0


def test_pace_override_wins(monkeypatch):
    fi, _ = _patch(monkeypatch)
    eval_gate.run(_args(set_="intent", pace=1.5))
    assert fi.calls[0]["pace"] == 1.5


def test_registered_and_schedulable():
    from backend.jobs.registry import JOBS
    assert "eval-gate" in JOBS and JOBS["eval-gate"].schedulable is True


def _fake_chitchat_eval(violations=0, n=16):
    async def fn(llm, pace=0.0):
        fn.calls.append({"pace": pace})
        fails = [{"text": "x", "response": "Đã tạo", "matched_markers": ["đã tạo"]}
                 for _ in range(violations)]
        return {"set": "chitchat", "n": n, "violations": violations, "fails": fails}
    fn.calls = []
    return fn


def test_chitchat_zero_violations_passes(monkeypatch):
    fchat = _fake_chitchat_eval(violations=0)
    monkeypatch.setitem(eval_gate.EVAL_FN, "chitchat", fchat)
    monkeypatch.setattr(run_eval, "_llm", lambda m: object())
    result = eval_gate.run(_args(set_="chitchat"))
    assert result.exit_code == PASS and result.verdict == "PASS"
    assert result.detail["chitchat"]["gate"] == "PASS"
    assert result.detail["chitchat"]["violations"] == 0


def test_chitchat_nonzero_violations_fails(monkeypatch):
    fchat = _fake_chitchat_eval(violations=2)
    monkeypatch.setitem(eval_gate.EVAL_FN, "chitchat", fchat)
    monkeypatch.setattr(run_eval, "_llm", lambda m: object())
    result = eval_gate.run(_args(set_="chitchat"))
    assert result.exit_code == GATE_FAIL and result.verdict == "FAIL"
    assert result.detail["chitchat"]["violations"] == 2


def test_chitchat_never_reads_a_baseline_file(monkeypatch, tmp_path):
    fchat = _fake_chitchat_eval(violations=0)
    monkeypatch.setitem(eval_gate.EVAL_FN, "chitchat", fchat)
    monkeypatch.setattr(run_eval, "_llm", lambda m: object())
    # BASELINES không có "chitchat" — nếu code lỡ tra cứu, KeyError sẽ lộ ra
    # thành INFRA_ERROR thay vì PASS. Assert PASS tức là đường code không đọc.
    assert "chitchat" not in eval_gate.BASELINES
    result = eval_gate.run(_args(set_="chitchat"))
    assert result.exit_code == PASS
    assert "baseline_acc" not in result.detail["chitchat"]
    assert "acc" not in result.detail["chitchat"]


def test_both_still_excludes_chitchat(monkeypatch):
    fi, fc = _patch(monkeypatch)
    fchat = _fake_chitchat_eval(violations=0)
    monkeypatch.setitem(eval_gate.EVAL_FN, "chitchat", fchat)
    result = eval_gate.run(_args(set_="both"))
    assert set(result.detail) == {"intent", "confirm"}
    assert fchat.calls == []


def test_chitchat_model_resolution_uses_chitchat_role(monkeypatch):
    fchat = _fake_chitchat_eval(violations=0)
    monkeypatch.setitem(eval_gate.EVAL_FN, "chitchat", fchat)
    monkeypatch.setattr(run_eval, "_llm", lambda m: object())
    monkeypatch.delenv("MODEL_CHITCHAT", raising=False)
    monkeypatch.delenv("AGENT_MODEL", raising=False)
    result = eval_gate.run(_args(set_="chitchat"))
    assert result.detail["chitchat"]["model"] == "qwen3:8b"   # default local


def test_chitchat_registered_as_valid_set_choice():
    # add_args đăng ký choices cho --set — verify "chitchat" có mặt bằng cách
    # dựng parser thật và parse.
    import argparse as _argparse
    p = _argparse.ArgumentParser()
    eval_gate.add_args(p)
    ns = p.parse_args(["--set", "chitchat"])
    assert ns.set == "chitchat"
