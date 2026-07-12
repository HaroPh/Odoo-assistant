# backend/tests/rag/test_reranker.py
"""Reranker fail-open + kill-switch — spec 2026-07-12 §3.2/§4/§5.1-2.
Unit thuần (mock _load) — KHÔNG cần DATABASE_URL, KHÔNG tải model thật."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

import pytest
import torch

from backend.src.rag import reranker


@pytest.fixture(autouse=True)
def _reset_state():
    reranker._state.update(model=None, tokenizer=None)
    yield
    reranker._state.update(model=None, tokenizer=None)


def test_disabled_returns_none_without_loading(monkeypatch):
    monkeypatch.setenv("RAG_RERANK_ENABLED", "0")
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise AssertionError("không được load model khi disabled")

    monkeypatch.setattr(reranker, "_load", boom)
    assert reranker.score_pairs("q", ["a"]) is None
    assert calls["n"] == 0


def test_loader_failure_fails_open_and_caches(monkeypatch):
    # Lỗi load (vd chưa có mạng lần tải đầu) → None, KHÔNG raise; lần gọi 2
    # dùng sentinel hỏng — không thử tải lại 2.3GB mỗi query (spec §4).
    monkeypatch.setenv("RAG_RERANK_ENABLED", "1")
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("no network")

    monkeypatch.setattr(reranker, "_load", boom)
    assert reranker.score_pairs("q", ["a"]) is None
    assert reranker.score_pairs("q", ["a"]) is None
    assert calls["n"] == 1
    assert reranker._state["model"] is False


class _FakeTok:
    def __call__(self, pairs, **kw):
        return {}


class _FakeModel:
    def __init__(self, logits):
        self._logits = logits

    def eval(self):
        pass

    def __call__(self, **inputs):
        class _R:
            pass

        r = _R()
        r.logits = self._logits
        return r


def test_happy_path_returns_floats(monkeypatch):
    monkeypatch.setenv("RAG_RERANK_ENABLED", "1")
    monkeypatch.setattr(reranker, "_load",
                        lambda: (_FakeModel(torch.tensor([0.2, 0.9])), _FakeTok()))
    scores = reranker.score_pairs("q", ["a", "b"])
    assert scores == [pytest.approx(0.2), pytest.approx(0.9)]


def test_score_count_mismatch_fails_open(monkeypatch):
    # Model trả số điểm ≠ số text → coi như hỏng (spec §4 bảng dòng 4).
    monkeypatch.setenv("RAG_RERANK_ENABLED", "1")
    monkeypatch.setattr(reranker, "_load",
                        lambda: (_FakeModel(torch.tensor([1.0])), _FakeTok()))
    assert reranker.score_pairs("q", ["a", "b"]) is None
    assert reranker._state["model"] is False


@pytest.mark.skipif(not os.environ.get("RUN_RERANK_MODEL"),
                    reason="tải model 2.3GB — chạy tay: set RUN_RERANK_MODEL=1")
def test_real_model_scores_relevance(monkeypatch):
    monkeypatch.setenv("RAG_RERANK_ENABLED", "1")
    scores = reranker.score_pairs(
        "khách hàng muốn hoàn hàng sau 30 ngày",
        ["Khách hàng có thể hoàn hàng trong vòng 30 ngày kể từ ngày mua.",
         "Quy trình bảo trì máy CNC định kỳ 6 tháng."])
    assert scores is not None
    assert scores[0] > scores[1]
