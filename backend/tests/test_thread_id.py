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
