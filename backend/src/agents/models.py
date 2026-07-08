# backend/src/agents/models.py
"""Registry model theo vai trò (ADR-009: M4 registry + QĐ M2 privacy split).

MỌI model ID đi qua đây — zero hardcode trong logic nghiệp vụ (bài học ChangAI
F8: 7 literal rải 5 file, có ID không tồn tại). Đổi model = đổi env, không đổi code.

QĐ M2 (ADR-009 §4 #7, KHÓA): chỉ router/evaluator/chitchat (mang tin nhắn thô)
được phép trỏ cloud. read/planner/fusion/synthesis mang dữ liệu nghiệp vụ
(tên khách, đơn, tồn kho, tài liệu nội bộ) → LUÔN local. Ép TẠI ĐÂY, ở tầng
thực thi (khóa #6) — env override cho role mang dữ liệu bị bỏ qua có chủ đích.

Mặc định 100% local (AGENT_MODEL) — behavior không đổi khi chưa flip A3.
"""
import os

from langchain_openai import ChatOpenAI

LITELLM_URL = os.environ.get("LITELLM_URL", "http://localhost:4000/v1")
LITELLM_KEY = os.environ.get("LITELLM_MASTER_KEY", "")

ROLES = ("router", "read", "planner", "fusion", "synthesis", "chitchat", "evaluator")

# QĐ M2: role được phép flip cloud. Role ngoài danh sách LUÔN local.
CLOUD_ALLOWED = frozenset({"router", "evaluator", "chitchat"})


def default_model() -> str:
    return os.environ.get("AGENT_MODEL", "qwen3:8b")


def is_qwen(name) -> bool:
    """Family check — quyết định timeout (local 120s vs cloud 30s)."""
    return "qwen" in (name or "").lower()


def model_for(role: str) -> str:
    """Model cho role. Role mang dữ liệu bị PIN vào local — không có knob."""
    if role not in CLOUD_ALLOWED:
        return default_model()
    return os.environ.get(f"MODEL_{role.upper()}", default_model())


def make_llm(role: str) -> ChatOpenAI:
    name = model_for(role)
    timeout = 120 if is_qwen(name) else 30    # spec §2.2#4
    return ChatOpenAI(model=name, base_url=LITELLM_URL, api_key=LITELLM_KEY,
                      temperature=0, timeout=timeout)


def make_llms() -> dict:
    return {role: make_llm(role) for role in ROLES}


def llms_from_single(llm) -> dict:
    """Back-compat cho test/caller cũ: mọi role dùng chung 1 llm."""
    return {role: llm for role in ROLES}
