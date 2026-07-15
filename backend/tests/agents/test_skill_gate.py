import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents import skill_gate


def test_unset_defaults_off(monkeypatch):
    monkeypatch.delenv("ERP_SKILLS_ENABLED", raising=False)
    assert skill_gate.skills_enabled() is False


def test_explicit_zero_is_off(monkeypatch):
    monkeypatch.setenv("ERP_SKILLS_ENABLED", "0")
    assert skill_gate.skills_enabled() is False


def test_explicit_one_is_on(monkeypatch):
    monkeypatch.setenv("ERP_SKILLS_ENABLED", "1")
    assert skill_gate.skills_enabled() is True


def test_garbage_value_is_off(monkeypatch):
    monkeypatch.setenv("ERP_SKILLS_ENABLED", "true")
    assert skill_gate.skills_enabled() is False
