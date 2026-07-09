# backend/tests/jobs/conftest.py
"""Isolation cho test jobs: LOGS_DIR trỏ tmp_path, JOBS dict snapshot/restore
(job thật đăng ký lúc import không rò giữa các test)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.jobs import registry


@pytest.fixture(autouse=True)
def _isolate_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "LOGS_DIR", tmp_path / "jobs")
    saved = dict(registry.JOBS)
    yield
    registry.JOBS.clear()
    registry.JOBS.update(saved)
