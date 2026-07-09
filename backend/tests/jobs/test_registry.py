# backend/tests/jobs/test_registry.py
import dataclasses
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.jobs.registry import (GATE_FAIL, INFRA_ERROR, PASS, Job, JobResult,
                                   JOBS, LOGS_DIR, register, write_result)
from backend.jobs import registry


def test_exit_contract_constants():
    assert (PASS, GATE_FAIL, INFRA_ERROR) == (0, 1, 2)


def test_register_adds_to_jobs():
    job = Job("fake", lambda a: None, "mô tả", schedulable=True)
    register(job)
    assert JOBS["fake"] is job


def test_write_result_creates_json(tmp_path, monkeypatch):
    monkeypatch.setattr(registry, "LOGS_DIR", tmp_path / "jobs")
    result = JobResult("fake", PASS, "PASS", {"k": "v"},
                       started_at="2026-07-09T23:00:00", duration_s=1.5)
    path = registry.write_result(result)
    assert path.parent == tmp_path / "jobs"
    assert path.name.startswith("fake-")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == dataclasses.asdict(result)
