import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import backend.tests.live_verify_common as lvc


def test_has_tool_leak_detects_known_tool_names():
    assert lvc.has_tool_leak("Không thể dùng receive_order vì thiếu mã đơn.") == ["receive_order"]


def test_has_tool_leak_clean_text_returns_empty():
    assert lvc.has_tool_leak("Bạn cần cung cấp mã đơn mua để tiếp tục.") == []


def test_has_tool_leak_case_insensitive():
    assert lvc.has_tool_leak("KHÔNG DÙNG FLAG_ORDER_FOR_REVIEW") == ["flag_order_for_review"]


def test_has_tool_leak_multiple_markers():
    text = "Không dùng receive_order, cũng không dùng flag_order_for_review."
    assert set(lvc.has_tool_leak(text)) == {"receive_order", "flag_order_for_review"}


def test_drive_conversation_sends_final_answer_on_confirm_marker(monkeypatch):
    script = iter(["Bạn cần cho biết số lượng?",
                   "Xác nhận NHẬN HÀNG cho đơn P001?", "Đã nhận hàng."])
    monkeypatch.setattr(lvc, "chat", lambda h, s, m: next(script))
    result = lvc.drive_conversation(
        [], "sid", "mở đầu",
        responders=[(lambda low: "số lượng" in low, "50")],
        final_answer="có")
    assert result.completed is True
    assert result.turns == 3
    assert result.final_answer == "Đã nhận hàng."
    assert result.all_answers == ["Bạn cần cho biết số lượng?",
                                  "Xác nhận NHẬN HÀNG cho đơn P001?", "Đã nhận hàng."]


def test_drive_conversation_stops_when_no_responder_matches(monkeypatch):
    monkeypatch.setattr(lvc, "chat", lambda h, s, m: "Câu trả lời không khớp gì cả.")
    result = lvc.drive_conversation([], "sid", "mở đầu", responders=[], final_answer="có")
    assert result.completed is False
    assert result.turns == 1


def test_drive_conversation_stops_at_max_turns(monkeypatch):
    monkeypatch.setattr(lvc, "chat", lambda h, s, m: "hỏi tiếp")
    result = lvc.drive_conversation(
        [], "sid", "mở đầu",
        responders=[(lambda low: True, "trả lời")],
        final_answer="có", max_turns=3)
    assert result.completed is False
    assert result.turns == 3


def test_drive_conversation_discount_style_confirm_marker(monkeypatch):
    # confirm_markers mặc định ("xác nhận",) phải khớp CẢ 2 dạng câu hỏi thật:
    # discount_quote's "...Xác nhận? (có / không)" và warehouse/delivery's câu
    # ngắn hơn — case này verify dạng discount_quote.
    script = iter(["Tổng sau chiết khấu: 304\nXác nhận? (có / không)", "Đã tạo báo giá."])
    monkeypatch.setattr(lvc, "chat", lambda h, s, m: next(script))
    result = lvc.drive_conversation([], "sid", "mở đầu", responders=[], final_answer="có")
    assert result.completed is True and result.turns == 2


def test_drive_conversation_ignores_clarification_containing_confirm_word(monkeypatch):
    # Regression: Task 2 live-run (e2e-skill-discount, 2026-07-17) found this
    # exact real assistant reply treated as a false-positive confirm-gate —
    # "xác nhận" used as an ordinary verb mid-sentence in a product-name
    # clarification question, with NO "?" anywhere in the message. Must NOT
    # be treated as the real money-confirm gate.
    clarification = ("Có vẻ tên sản phẩm 'Large Cabinet' vẫn bị trùng. Vui lòng "
                     "xác nhận chính xác tên sản phẩm (ví dụ: [E-COM07] Large "
                     "Cabinet) hoặc cung cấp mã sản phẩm để chúng tôi tìm đúng "
                     "sản phẩm cần báo giá.")
    monkeypatch.setattr(lvc, "chat", lambda h, s, m: clarification)
    result = lvc.drive_conversation(
        [], "sid", "mở đầu", responders=[], final_answer="có", max_turns=2)
    assert result.completed is False
    assert result.turns == 1


def test_drive_fixed_turns_returns_all_answers_in_order(monkeypatch):
    script = iter(["đáp 1", "đáp 2", "đáp 3"])
    monkeypatch.setattr(lvc, "chat", lambda h, s, m: next(script))
    answers = lvc.drive_fixed_turns([], "sid", "mở đầu", ["theo sau 1", "theo sau 2"])
    assert answers == ["đáp 1", "đáp 2", "đáp 3"]


def test_print_result_all_passed_returns_true(capsys):
    scenarios = [lvc.Scenario("a", True, 3, "ok"), lvc.Scenario("b", True, 2, "ok")]
    assert lvc.print_result("job-x", scenarios) is True
    out = capsys.readouterr().out
    assert "RESULT_JSON" in out and '"passed": 2' in out


def test_print_result_some_failed_returns_false(capsys):
    scenarios = [lvc.Scenario("a", True, 3, "ok"), lvc.Scenario("b", False, 8, "timeout")]
    assert lvc.print_result("job-x", scenarios) is False


def test_load_env_sets_missing_keys_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TEST_LVC_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_LVC_KEY=hello\n# comment\n\nOTHER=1\n", encoding="utf-8")
    lvc.load_env(str(env_file))
    assert os.environ["TEST_LVC_KEY"] == "hello"


def test_load_env_does_not_override_existing_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_LVC_KEY2", "already-set")
    env_file = tmp_path / ".env"
    env_file.write_text("TEST_LVC_KEY2=from-file\n", encoding="utf-8")
    lvc.load_env(str(env_file))
    assert os.environ["TEST_LVC_KEY2"] == "already-set"
