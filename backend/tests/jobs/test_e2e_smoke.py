# backend/tests/jobs/test_e2e_smoke.py
"""e2e-smoke: preflight chặn khi stack chết, subprocess map exit code, on-demand only."""
import argparse
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.jobs import e2e_smoke
from backend.jobs.registry import GATE_FAIL, INFRA_ERROR, PASS


def _args():
    return argparse.Namespace()


def test_preflight_fail_exits_two_without_subprocess(monkeypatch):
    monkeypatch.setattr(e2e_smoke, "_preflight",
                        lambda: "backend :8000 không chạy")
    def no_subprocess(*a, **kw):
        raise AssertionError("subprocess không được gọi khi preflight fail")
    monkeypatch.setattr(e2e_smoke.subprocess, "run", no_subprocess)
    result = e2e_smoke.run(_args())
    assert result.exit_code == INFRA_ERROR and result.verdict == "ERROR"
    assert "8000" in result.detail["preflight"]


def test_subprocess_zero_maps_pass(monkeypatch):
    monkeypatch.setattr(e2e_smoke, "_preflight", lambda: None)
    monkeypatch.setattr(e2e_smoke.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=0,
                                                         stdout="OK", stderr=""))
    result = e2e_smoke.run(_args())
    assert result.exit_code == PASS and result.verdict == "PASS"


def test_subprocess_nonzero_maps_fail(monkeypatch):
    monkeypatch.setattr(e2e_smoke, "_preflight", lambda: None)
    monkeypatch.setattr(e2e_smoke.subprocess, "run",
                        lambda *a, **kw: SimpleNamespace(returncode=1,
                                                         stdout="s1 FAIL", stderr=""))
    result = e2e_smoke.run(_args())
    assert result.exit_code == GATE_FAIL and result.verdict == "FAIL"
    assert "s1 FAIL" in result.detail["stdout"]


def test_subprocess_timeout_maps_infra_error(monkeypatch):
    monkeypatch.setattr(e2e_smoke, "_preflight", lambda: None)
    def fake_run(*a, **kw):
        raise e2e_smoke.subprocess.TimeoutExpired(cmd="fake", timeout=600)
    monkeypatch.setattr(e2e_smoke.subprocess, "run", fake_run)
    result = e2e_smoke.run(_args())
    assert result.exit_code == INFRA_ERROR and result.verdict == "ERROR"
    assert "timeout" in result.detail["error"].lower()


def test_registered_not_schedulable():
    from backend.jobs.registry import JOBS
    assert "e2e-smoke" in JOBS and JOBS["e2e-smoke"].schedulable is False
