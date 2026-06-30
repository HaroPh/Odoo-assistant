import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from backend.src.rag import embed as embed_mod


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
    def json(self):
        return self._payload
    def raise_for_status(self):
        if self.status_code != 200:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)


def test_embed_texts_posts_model_and_returns_vectors(monkeypatch):
    captured = {}
    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp({"embeddings": [[0.1] * 1024, [0.2] * 1024]})
    monkeypatch.setattr(embed_mod.httpx, "post", fake_post)

    out = embed_mod.embed_texts(["a", "b"])
    assert captured["json"]["model"] == "bge-m3"
    assert captured["json"]["input"] == ["a", "b"]
    assert captured["url"].endswith("/api/embed")
    assert len(out) == 2 and len(out[0]) == 1024


def test_embed_query_returns_single_vector(monkeypatch):
    monkeypatch.setattr(embed_mod.httpx, "post",
                        lambda url, json, timeout: _FakeResp({"embeddings": [[0.3] * 1024]}))
    v = embed_mod.embed_query("xin chào")
    assert len(v) == 1024


def test_embed_raises_on_missing_embeddings(monkeypatch):
    monkeypatch.setattr(embed_mod.httpx, "post",
                        lambda url, json, timeout: _FakeResp({"error": "model not found"}))
    with pytest.raises(embed_mod.EmbeddingError):
        embed_mod.embed_texts(["a"])
