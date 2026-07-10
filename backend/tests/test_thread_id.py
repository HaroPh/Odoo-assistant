import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # backend/ on path
from src.main import _derive_thread_id


def test_explicit_session_id_wins():
    assert _derive_thread_id({"session_id": "abc"}, []) == "abc"


def test_explicit_id_used():
    assert _derive_thread_id({"id": "req-1"}, []) == "req-1"


def test_stable_across_turns_of_one_conversation():
    # Open WebUI resends full history each turn with NO session id; the first user
    # message is constant, so the thread must be identical across turns.
    m1 = [{"role": "user", "content": "tạo báo giá cho Azure, 2 Large Cabinet"}]
    m2 = m1 + [{"role": "assistant", "content": "Xác nhận? (có / không)"},
               {"role": "user", "content": "có"}]
    t1 = _derive_thread_id({}, m1)
    t2 = _derive_thread_id({}, m2)
    assert t1 == t2 and t1.startswith("conv-")


def test_different_conversations_differ():
    a = _derive_thread_id({}, [{"role": "user", "content": "xin chào"}])
    b = _derive_thread_id({}, [{"role": "user", "content": "tạo báo giá"}])
    assert a != b


def test_no_user_message_returns_none():
    assert _derive_thread_id({}, []) is None


# ── R7 Lớp A: Open WebUI identity headers ─────────────────────────────────────

def test_header_chat_id_with_user_id():
    headers = {"x-openwebui-chat-id": "chat-123", "x-openwebui-user-id": "u-9"}
    assert _derive_thread_id({}, [], headers=headers) == "owui:u-9:chat-123"


def test_header_chat_id_without_user_id():
    headers = {"x-openwebui-chat-id": "chat-123"}
    assert _derive_thread_id({}, [], headers=headers) == "owui:anon:chat-123"


def test_header_beats_explicit_body_session():
    headers = {"x-openwebui-chat-id": "chat-123", "x-openwebui-user-id": "u-9"}
    assert _derive_thread_id({"session_id": "abc"}, [],
                             headers=headers) == "owui:u-9:chat-123"


def test_user_id_alone_does_not_derive_thread():
    # user-id không có chat-id thì không định danh được HỘI THOẠI → rơi xuống
    # chuỗi ưu tiên cũ.
    headers = {"x-openwebui-user-id": "u-9"}
    assert _derive_thread_id({"session_id": "abc"}, [], headers=headers) == "abc"


def test_absent_headers_keep_old_behavior():
    m = [{"role": "user", "content": "xin chào"}]
    assert _derive_thread_id({}, m, headers=None) == _derive_thread_id({}, m)
    assert _derive_thread_id({}, m, headers={}) == _derive_thread_id({}, m)


# ── R7 Lớp B: _explicit_session + endpoint wiring ─────────────────────────────
from fastapi.testclient import TestClient

import src.main as main_mod
from src.main import _explicit_session


def test_explicit_session_detection():
    assert _explicit_session({"session_id": "s"}) is True
    assert _explicit_session({"id": "i"}) is True
    assert _explicit_session({}) is False


class _RecordingAgent:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, thread_id=None, reset_if_fresh=False):
        self.calls.append({"thread_id": thread_id,
                           "reset_if_fresh": reset_if_fresh})
        return "ok"


def _post_chat(json_body, headers=None):
    agent = _RecordingAgent()
    main_mod._state["agent"] = agent
    try:
        client = TestClient(main_mod.app)  # không dùng `with` → lifespan KHÔNG chạy
        resp = client.post("/v1/chat/completions", json=json_body,
                           headers=headers or {})
        assert resp.status_code == 200
        return agent.calls[0]
    finally:
        main_mod._state.pop("agent", None)


def test_endpoint_derived_thread_enables_reset():
    call = _post_chat({"messages": [{"role": "user", "content": "xin chào"}],
                       "stream": False})
    assert call["thread_id"].startswith("conv-")
    assert call["reset_if_fresh"] is True


def test_endpoint_explicit_session_disables_reset():
    call = _post_chat({"messages": [{"role": "user", "content": "có"}],
                       "session_id": "s1", "stream": False})
    assert call["thread_id"] == "s1"
    assert call["reset_if_fresh"] is False


def test_endpoint_owui_headers_win_and_enable_reset():
    call = _post_chat({"messages": [{"role": "user", "content": "xin chào"}],
                       "stream": False},
                      headers={"X-OpenWebUI-Chat-Id": "c1",
                               "X-OpenWebUI-User-Id": "u1"})
    assert call["thread_id"] == "owui:u1:c1"
    assert call["reset_if_fresh"] is True


# ── R7 hotfix (live-verify 2026-07-09): Open WebUI's own background task calls
# (title/tags/follow-up/query generation) share the SAME x-openwebui-chat-id
# header as real user turns and are always a single user message with no
# session_id — indistinguishable from a real "fresh conversation" by headers
# alone. Detect them by Open WebUI's stable internal prompt prefix so they
# never touch thread/checkpoint state (never wipe a real parked confirm).
from src.main import _is_owui_task_prompt


def test_task_prompt_detected_single_message():
    msgs = [{"role": "user", "content": "### Task:\nSuggest 3-5 relevant follow-up "
                                        "questions or prompts..."}]
    assert _is_owui_task_prompt(msgs) is True


def test_normal_message_not_task_prompt():
    assert _is_owui_task_prompt([{"role": "user", "content": "xin chào"}]) is False


def test_task_prefix_in_multi_turn_history_not_task_prompt():
    # A real user could paste "### Task:" text mid-conversation; only a
    # SOLE message with this prefix (Open WebUI's own single-shot call
    # shape) is treated as a task prompt.
    msgs = [{"role": "user", "content": "### Task:\nfollow-up..."},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "tiếp tục"}]
    assert _is_owui_task_prompt(msgs) is False


def test_empty_messages_not_task_prompt():
    assert _is_owui_task_prompt([]) is False


def test_prefix_without_newline_not_task_prompt():
    # Tightened match (I3, review 2026-07-09): requires the newline Open
    # WebUI's real template always has right after "Task:", narrowing the
    # false-positive surface for a real user message that merely starts
    # with the words "### Task:" (e.g. pasted markdown) without the newline.
    msgs = [{"role": "user", "content": "### Task: is this urgent?"}]
    assert _is_owui_task_prompt(msgs) is False


def test_none_content_does_not_crash():
    # M4 (review 2026-07-09): a message dict with content=None explicitly
    # (distinct from a missing key) must not crash .startswith on None.
    msgs = [{"role": "user", "content": None}]
    assert _is_owui_task_prompt(msgs) is False


def test_real_message_matching_task_prefix_is_documented_tradeoff():
    # I3 (review 2026-07-09): a real user's FIRST message that happens to
    # match the exact prefix+newline is still routed to the stateless path
    # (accepted residual risk, documented in spec §8) — it costs one ERP
    # turn, but critically does NOT wipe any state either way.
    msgs = [{"role": "user", "content": "### Task:\ncó phải deadline hôm nay không?"}]
    assert _is_owui_task_prompt(msgs) is True


class _RecordingAgentWithStateless(_RecordingAgent):
    def __init__(self):
        super().__init__()
        self.stateless_calls = []

    async def answer_stateless(self, content):
        self.stateless_calls.append(content)
        return "stateless-ok"


def test_endpoint_task_prompt_skips_thread_and_chat():
    agent = _RecordingAgentWithStateless()
    main_mod._state["agent"] = agent
    try:
        client = TestClient(main_mod.app)
        resp = client.post("/v1/chat/completions", json={
            "messages": [{"role": "user",
                         "content": "### Task:\nSuggest 3-5 relevant follow-up "
                                    "questions..."}],
            "stream": False},
            headers={"X-OpenWebUI-Chat-Id": "c1", "X-OpenWebUI-User-Id": "u1"})
        assert resp.status_code == 200
        assert agent.calls == []  # agent.chat NEVER called — no thread touched
        assert len(agent.stateless_calls) == 1
        assert agent.stateless_calls[0].startswith("### Task:")
    finally:
        main_mod._state.pop("agent", None)


def test_endpoint_normal_message_still_uses_chat_not_stateless():
    agent = _RecordingAgentWithStateless()
    main_mod._state["agent"] = agent
    try:
        client = TestClient(main_mod.app)
        resp = client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "xin chào"}], "stream": False},
            headers={"X-OpenWebUI-Chat-Id": "c1", "X-OpenWebUI-User-Id": "u1"})
        assert resp.status_code == 200
        assert agent.stateless_calls == []
        assert len(agent.calls) == 1
    finally:
        main_mod._state.pop("agent", None)


# ── Finding 2 (live-test 2026-07-10): agent.chat/answer_stateless lỗi thoáng
# qua (vd cloud LLM hiccup) trước đây không được bắt → FastAPI trả 500 thô,
# retry mới thành công. rag_node/fusion_node đều degrade về SAFE_MSG khi lỗi;
# endpoint phải làm tương tự thay vì để lỗi rơi tới tận response layer.

class _ExplodingAgent:
    async def chat(self, messages, thread_id=None, reset_if_fresh=False):
        raise ConnectionError("cloud LLM hiccup")

    async def answer_stateless(self, content):
        raise ConnectionError("cloud LLM hiccup")


def test_endpoint_survives_agent_exception_returns_200_not_500():
    main_mod._state["agent"] = _ExplodingAgent()
    try:
        client = TestClient(main_mod.app)
        resp = client.post("/v1/chat/completions", json={
            "messages": [{"role": "user", "content": "cảm ơn nhé"}], "stream": False})
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"]
        assert content == main_mod.ERROR_MSG
    finally:
        main_mod._state.pop("agent", None)


def test_endpoint_exception_is_logged_with_traceback(caplog):
    import logging
    main_mod._state["agent"] = _ExplodingAgent()
    try:
        client = TestClient(main_mod.app)
        with caplog.at_level(logging.ERROR, logger="src.main"):
            client.post("/v1/chat/completions", json={
                "messages": [{"role": "user", "content": "cảm ơn nhé"}], "stream": False})
        assert any("cloud LLM hiccup" in r.message or
                  (r.exc_info and "cloud LLM hiccup" in str(r.exc_info[1]))
                  for r in caplog.records)
    finally:
        main_mod._state.pop("agent", None)


def test_endpoint_survives_exception_in_stateless_task_prompt_path():
    main_mod._state["agent"] = _ExplodingAgent()
    try:
        client = TestClient(main_mod.app)
        resp = client.post("/v1/chat/completions", json={
            "messages": [{"role": "user",
                         "content": "### Task:\nSuggest a title..."}],
            "stream": False})
        assert resp.status_code == 200
        assert resp.json()["choices"][0]["message"]["content"] == main_mod.ERROR_MSG
    finally:
        main_mod._state.pop("agent", None)
