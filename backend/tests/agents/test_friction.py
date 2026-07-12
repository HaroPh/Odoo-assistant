# backend/tests/agents/test_friction.py
"""Sink JSONL fail-open cho planner friction — spec 2026-07-12 §5.1-4."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.friction import log_friction


def test_writes_one_parseable_line_with_vietnamese(tmp_path, monkeypatch):
    p = tmp_path / "friction.jsonl"
    monkeypatch.setenv("FRICTION_LOG_PATH", str(p))
    log_friction({"outcome": "fail", "excerpt": "tạo báo giá"})
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    ev = json.loads(lines[0])
    assert ev["excerpt"] == "tạo báo giá"
    # ensure_ascii=False — tiếng Việt nguyên văn, không ạ escape
    assert "tạo báo giá" in lines[0]


def test_append_two_calls_two_lines(tmp_path, monkeypatch):
    p = tmp_path / "friction.jsonl"
    monkeypatch.setenv("FRICTION_LOG_PATH", str(p))
    log_friction({"outcome": "raw"})
    log_friction({"outcome": "salvage"})
    assert len(p.read_text(encoding="utf-8").splitlines()) == 2


def test_fail_open_on_unwritable_path(tmp_path, monkeypatch):
    # Path cha là 1 FILE → mkdir/open chắc chắn lỗi → log_friction phải nuốt,
    # tuyệt đối không raise (observability không được chạm planner).
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("FRICTION_LOG_PATH", str(blocker / "sub" / "f.jsonl"))
    log_friction({"outcome": "raw"})  # không raise là pass


def test_creates_missing_parent_dirs(tmp_path, monkeypatch):
    p = tmp_path / "a" / "b" / "friction.jsonl"
    monkeypatch.setenv("FRICTION_LOG_PATH", str(p))
    log_friction({"outcome": "raw"})
    assert p.exists()
