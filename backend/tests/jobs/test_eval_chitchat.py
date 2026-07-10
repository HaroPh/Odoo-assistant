"""eval_chitchat: gate tuyệt đối chống model bịa đã thực hiện hành động ERP
(respond_unknown không bind tool nào — mọi khẳng định 'đã làm X' là bịa)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.evals import run_eval
from backend.evals.cases import CHITCHAT_CASES, HALLUCINATION_MARKERS


class _RecordingLLM:
    """Fake LLM ghi lại messages đã nhận, trả reply cố định hoặc theo map."""
    def __init__(self, reply="Xin chào! Mình giúp được gì cho bạn?", reply_map=None):
        self._reply = reply
        self._reply_map = reply_map or {}
        self.calls = []

    async def ainvoke(self, messages):
        self.calls.append(messages)
        text = messages[-1].content
        reply = self._reply_map.get(text, self._reply)
        class R:
            content = reply
        return R()


async def test_no_violation_on_safe_reply():
    llm = _RecordingLLM(reply="Mình chưa rõ ý bạn, bạn cần hỗ trợ gì ạ?")
    result = await run_eval.eval_chitchat(llm)
    assert result["set"] == "chitchat"
    assert result["n"] == len(CHITCHAT_CASES)
    assert result["violations"] == 0
    assert result["fails"] == []


async def test_violation_detected_and_reported():
    llm = _RecordingLLM(reply="Đã tạo báo giá thành công cho bạn!")
    result = await run_eval.eval_chitchat(llm)
    assert result["violations"] == len(CHITCHAT_CASES)   # mọi case đều dính
    first = result["fails"][0]
    assert first["response"] == "Đã tạo báo giá thành công cho bạn!"
    assert "đã tạo" in first["matched_markers"]


async def test_bare_da_is_not_a_false_positive():
    # "đã" một mình (thì quá khứ thông thường) KHÔNG được match — chỉ cụm từ
    # cụ thể trong HALLUCINATION_MARKERS mới tính là violation.
    llm = _RecordingLLM(reply="Mình đã hiểu câu hỏi của bạn rồi nhé!")
    result = await run_eval.eval_chitchat(llm)
    assert result["violations"] == 0


async def test_da_luu_y_is_not_a_false_positive():
    # Regression (task-review finding): marker "đã lưu" (bare) từng match
    # nhầm bên trong "đã lưu ý" (= đã ghi nhận, không liên quan hành động
    # ERP) — rất phổ biến trong hội thoại. Đã đổi sang cụm có tân ngữ cụ thể
    # ("đã lưu đơn"/"đã lưu thông tin"/"đã lưu thay đổi").
    llm = _RecordingLLM(reply="Mình đã lưu ý câu hỏi của bạn, cần hỗ trợ gì thêm không?")
    result = await run_eval.eval_chitchat(llm)
    assert result["violations"] == 0


async def test_marker_matching_is_case_insensitive():
    llm = _RecordingLLM(reply="ĐÃ XÁC NHẬN đơn hàng cho bạn.")
    result = await run_eval.eval_chitchat(llm)
    assert result["violations"] == len(CHITCHAT_CASES)


async def test_mirrors_respond_unknown_persona_system_prompt():
    # Khóa đúng hành vi respond_unknown thật (sau persona): SystemMessage
    # CHITCHAT_PROMPT + HumanMessage. Eval phải mirror y hệt production (khóa #10).
    from backend.src.agents.prompts import CHITCHAT_PROMPT
    llm = _RecordingLLM()
    await run_eval.eval_chitchat(llm)
    for messages in llm.calls:
        assert len(messages) == 2
        assert messages[0].type == "system"
        assert messages[0].content == CHITCHAT_PROMPT
        assert messages[1].type == "human"


async def test_pace_sleeps_between_calls(monkeypatch):
    sleeps = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await run_eval.eval_chitchat(_RecordingLLM(), pace=5.0)
    assert len(sleeps) == len(CHITCHAT_CASES) - 1
    assert all(s == 5.0 for s in sleeps)


async def test_default_pace_is_zero_backward_compat(monkeypatch):
    sleeps = []
    async def fake_sleep(s):
        sleeps.append(s)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    await run_eval.eval_chitchat(_RecordingLLM())   # không truyền pace
    assert sleeps == []


def test_chitchat_cases_reuse_intent_unknown_labels():
    from backend.evals.cases import INTENT_CASES
    unknown_texts = [t for t, label in INTENT_CASES if label == "unknown"]
    for t in unknown_texts:
        assert t in CHITCHAT_CASES


def test_hallucination_markers_are_specific_not_bare_da():
    assert "đã" not in HALLUCINATION_MARKERS
    assert all(len(m) > 2 for m in HALLUCINATION_MARKERS)   # cụm từ, không phải 1 âm tiết
