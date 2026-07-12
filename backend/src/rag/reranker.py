"""Cross-encoder reranker (spec 2026-07-12-rag-reranker-design §3.2).

BGE-Reranker-v2-m3 chạy CPU (torch build +cpu — CUDA không tồn tại trong
env này), lazy load ở lần gọi đầu. Fail-open tuyệt đối: mọi sự cố (chưa có
mạng lần tải model đầu, OOM, lỗi inference) → None và retrieval quay về
đúng hành vi hybrid-rrf hiện tại — không bao giờ tệ hơn hiện trạng, không
bao giờ raise vào rag_node. Trạng thái hỏng cache trong process (sentinel
False) — không thử tải lại model 2.3GB mỗi query; restart mới thử lại.

Kill-switch: env RAG_RERANK_ENABLED=0 (mặc định bật), đọc mỗi lần gọi.
"""
import logging
import os

from .config import RERANK_MODEL, RERANK_MAX_LENGTH

logger = logging.getLogger(__name__)

# "model": None = chưa load | False = hỏng (không thử lại) | object = sẵn sàng
_state: dict = {"model": None, "tokenizer": None}


def _load():
    """Load model + tokenizer (1 lần). Raise nếu lỗi — caller cache sentinel."""
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(RERANK_MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(RERANK_MODEL)
    model.eval()
    return model, tokenizer


def score_pairs(query: str, texts: list[str]) -> list[float] | None:
    """Điểm relevance từng cặp (query, text). None = tắt/hỏng (fail-open)."""
    if os.environ.get("RAG_RERANK_ENABLED", "1") == "0":
        return None
    if _state["model"] is False:
        return None
    try:
        if _state["model"] is None:
            _state["model"], _state["tokenizer"] = _load()
        import torch
        pairs = [[query, t] for t in texts]
        inputs = _state["tokenizer"](pairs, padding=True, truncation=True,
                                     max_length=RERANK_MAX_LENGTH,
                                     return_tensors="pt")
        with torch.no_grad():
            logits = _state["model"](**inputs).logits.view(-1)
        scores = [float(s) for s in logits]
        if len(scores) != len(texts):
            raise ValueError(f"expected {len(texts)} scores, got {len(scores)}")
        return scores
    except Exception:  # noqa: BLE001 — fail-open theo spec §4
        logger.warning("Reranker unavailable — falling back to hybrid-rrf order",
                       exc_info=True)
        _state["model"] = False
        return None
