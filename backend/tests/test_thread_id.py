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
