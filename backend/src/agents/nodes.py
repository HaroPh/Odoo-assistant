# backend/src/agents/nodes.py
import os
import re
import asyncio
import json
import time
import logging
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain.agents import create_agent as _create_agent
from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .prompts import (INTENT_ROUTER_PROMPT, SYSTEM_PROMPT, WRITE_PLANNER_PROMPT,
                      WRITE_CONFIRM_PREFIX, CHITCHAT_PROMPT, render_working_context)
from .write_registry import COORDINATED_TOOLS, expand_chain
from ..rag.retrieve import retrieve
from .synthesis import synthesize, SAFE_MSG
from .tool_result import _tool_result_text, parse_write_result
from .working_context import derive_working_context, enforce_explicit_ref
from . import write_gate

logger = logging.getLogger(__name__)

VALID_INTENTS = {"erp_read", "erp_write", "rag", "mixed", "unknown"}


def make_intent_router_node(llm):
    async def intent_router(state: ERPAgentState) -> dict:
        last_human = next(
            (m for m in reversed(state["messages"]) if m.type == "human"),
            None,
        )
        if not last_human:
            return {"intent": "unknown"}

        response = await llm.ainvoke([
            SystemMessage(content=INTENT_ROUTER_PROMPT),
            HumanMessage(content=last_human.content),
        ])
        intent = response.content.strip().lower()
        if intent not in VALID_INTENTS:
            intent = "unknown"
        return {"intent": intent}

    return intent_router


# ── erp_read ─────────────────────────────────────────────────────────────────

def make_erp_read_node(llm, tools):
    async def erp_read(state: ERPAgentState) -> dict:
        # Invariant A: MỘT system prompt hiệu dụng duy nhất. Context đặt TRƯỚC
        # SYSTEM_PROMPT để '/no_think' giữ vị trí cuối. Build agent per-call là
        # cách thoả invariant (chi phí ~ms, stack local); context KHÔNG được
        # chèn vào messages nên không thể leak vào state.
        wc = state.get("working_context")
        prompt = (render_working_context(wc) + "\n\n" + SYSTEM_PROMPT) \
            if wc else SYSTEM_PROMPT
        agent = _create_agent(llm, tools, system_prompt=prompt)
        result = await agent.ainvoke({"messages": state["messages"]})
        # Return only messages added by the agent (skip the input messages)
        new_msgs = result["messages"][len(state["messages"]):]
        return {"messages": new_msgs}

    return erp_read


# ── rag (doc-only answering) ──────────────────────────────────────────────────

def make_rag_node(llm):
    """Document Q&A: retrieve (sync, off the loop) → grounded synthesis + citations.

    retrieve() is sync psycopg; asyncio.to_thread keeps the event loop free.
    Any failure degrades to SAFE_MSG — the graph never crashes.
    """
    async def rag_node(state: ERPAgentState) -> dict:
        last_human = next(
            (m for m in reversed(state["messages"]) if m.type == "human"), None)
        if last_human is None:
            return {"messages": [AIMessage(content=SAFE_MSG)]}
        query = last_human.content
        try:
            result = await asyncio.to_thread(retrieve, query)
            answer = await synthesize(query, result, llm)
        except Exception:
            logger.exception("rag_node failed")
            answer = SAFE_MSG
        return {"messages": [AIMessage(content=answer)]}

    return rag_node


# ── respond_unknown ───────────────────────────────────────────────────────────

def make_respond_unknown_node(llm):
    async def respond_unknown(state: ERPAgentState) -> dict:
        # M5 (ADR-009): role chit-chat được phép chạy cloud (QĐ M2) → không
        # gửi full history — assistant-turn trước có thể mang dữ liệu ERP từ
        # erp_read. Chỉ gửi tin nhắn user cuối. Nếu KHÔNG có tin nhắn user
        # nào trong state (route hiếm gặp), KHÔNG gọi LLM — trả câu mặc
        # định, triệt tiêu hoàn toàn khả năng forward nội dung assistant-turn
        # ra ngoài.
        last_human = next(
            (m for m in reversed(state["messages"]) if m.type == "human"), None)
        if last_human is None:
            return {"messages": [AIMessage(content="Xin lỗi, bạn cần hỗ trợ gì?")]}
        response = await llm.ainvoke([SystemMessage(content=CHITCHAT_PROMPT), last_human])
        return {"messages": [response]}

    return respond_unknown


# ── erp_write_planner ─────────────────────────────────────────────────────────

# A5 redefined (spec 2026-07-10-a5-planner-json-retry): qwen3:8b là model họ
# thinking và WRITE_PLANNER_PROMPT không có /no_think — các dạng JSON hỏng
# dễ đoán (khối <think>, markdown fence) cứu được tất định trước khi tốn
# 1 call LLM sửa lỗi. Khóa #7 cấm escalate cloud; sau Phase B không còn
# model local thứ 2 → retry CÙNG model, đúng 1 lần.
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

_JSON_CORRECTION = (
    "Câu trả lời trên không phải JSON hợp lệ. Trả lời LẠI CHỈ bằng JSON "
    "đúng định dạng đã yêu cầu — không markdown fence, không giải thích, "
    "không text nào khác."
)


def _try_loads(text: str) -> dict | None:
    """json.loads trả dict, mọi thứ khác (parse fail / JSON không phải dict
    như list, số) → None — plan bắt buộc là object."""
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _parse_plan(raw: str) -> dict | None:
    """Parse pipeline 2 tầng (spec §3.1). Tầng 1: loads thẳng. Tầng 2:
    salvage tất định — strip mọi khối <think>…</think> rồi strip markdown
    fence nếu nó bọc TOÀN BỘ phần còn lại. KHÔNG brace-extract tùy tiện
    (có thể vớ JSON nháp trong khối think). None = không cứu được."""
    text = raw.strip()
    plan = _try_loads(text)
    if plan is not None:
        return plan
    stripped = _THINK_RE.sub("", text).strip()
    fence = _FENCE_RE.fullmatch(stripped)
    if fence:
        stripped = fence.group(1).strip()
    plan = _try_loads(stripped)
    if plan is not None:
        logger.info("Write planner JSON salvaged (fence/think strip)")
    return plan


async def _plan_json(llm, system: str, messages: list) -> dict | None:
    """Gọi planner LLM + parse, với đúng 1 lần corrective retry CÙNG model
    khi lần đầu không parse được (A5 redefined — khóa #7 cấm escalate cloud,
    không còn model local thứ 2 sau Phase B). 2 message sửa lỗi chỉ sống
    trong lời gọi này — không rò vào state["messages"] (spec §3.2)."""
    base = [SystemMessage(content=system), *messages]
    response = await llm.ainvoke(base)
    plan = _parse_plan(response.content)
    if plan is not None:
        return plan
    logger.warning("Write planner returned non-JSON: %s", response.content)
    retry = await llm.ainvoke([
        *base,
        AIMessage(content=response.content),
        HumanMessage(content=_JSON_CORRECTION),
    ])
    plan = _parse_plan(retry.content)
    if plan is not None:
        logger.info("Write planner JSON retry succeeded")
        return plan
    logger.warning("Write planner returned non-JSON after 2 attempts: %s",
                   retry.content)
    return None


def make_erp_write_planner_node(llm):
    async def erp_write_planner(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return {"messages": [AIMessage(
                content=(
                    "Tính năng ghi (tạo/sửa đơn hàng, cập nhật tồn kho) "
                    "chưa được kích hoạt trong phiên bản này."
                )
            )], "pending_action": None, "auto_chain": None}

        # Plan the action — invariant A: ONE effective system prompt; context
        # first so the JSON-format block stays last.
        wc = state.get("working_context")
        system = (render_working_context(wc) + "\n\n" + WRITE_PLANNER_PROMPT) \
            if wc else WRITE_PLANNER_PROMPT
        plan = await _plan_json(llm, system, state["messages"])
        if plan is None:
            return {"messages": [AIMessage(content="Không thể xác định thao tác cần thực hiện. Vui lòng mô tả rõ hơn.")],
                    "pending_action": None, "auto_chain": None}

        # Invariant C tầng 2: mã tường minh trong lời user thắng context.
        last_human = next((m.content for m in reversed(state["messages"])
                           if m.type == "human"), "")
        plan = enforce_explicit_ref(plan, last_human)

        # Chuỗi đa bước khai báo trước: validate tất định qua registry walk.
        # LLM bịa chain_until → None → single-step như cũ (fail-safe).
        chain = expand_chain(plan.get("tool"), plan.get("chain_until"))
        auto_chain = [t for t, _ in chain] if chain else None
        if chain:
            plan = {**plan, "chain_note":
                    "\n\nSau đó tự động: " + " → ".join(l for _, l in chain)}

        # Coordinated writes own their own resolution + confirm; don't interrupt here.
        if plan.get("tool") in COORDINATED_TOOLS:
            return {"pending_action": plan, "auto_chain": auto_chain}

        summary = plan.get("summary") or plan.get("tool") or "thao tác"
        # Invariant C tầng 3: hiện tool+args TẤT ĐỊNH — user luôn thấy ref thật
        # trước khi "có", kể cả khi summary của LLM mơ hồ.
        args_line = ", ".join(f"{k}={v}" for k, v in (plan.get("args") or {}).items())
        question = WRITE_CONFIRM_PREFIX + (f"**{summary}**\n"
                                           f"({plan.get('tool')}: {args_line})"
                                           f"{plan.get('chain_note') or ''}\n\n"
                                           f"Xác nhận? (có / không)")
        ttl = int(os.environ.get("CONFIRMATION_TTL_SECONDS", "300"))
        confirmed = _interrupt({
            "question": question,
            "action": plan,
            "expires_at": time.time() + ttl,
        })
        return {"pending_action": plan, "confirmed": confirmed,
                "auto_chain": auto_chain}

    return erp_write_planner


# ── erp_write_executor ────────────────────────────────────────────────────────

def make_erp_write_executor_node(tools):
    """Execute the confirmed write by invoking the named tool directly.

    Security (write-gate, rate-limit) lives in the MCP gateway; domain
    validation lives in the tool. Here we only route + fail safe so a bad
    plan never crashes the graph.
    """
    by_name = {t.name: t for t in tools}

    async def erp_write_executor(state: ERPAgentState) -> dict:
        cleared = {"pending_action": None, "confirmed": None, "last_write": None}
        if not state.get("confirmed"):
            return {"messages": [AIMessage(content="Đã hủy thao tác.")], **cleared}

        action = state.get("pending_action") or {}
        name = action.get("tool")
        tool = by_name.get(name)
        if tool is None:
            return {"messages": [AIMessage(
                content=f"Thao tác '{name}' không khả dụng."
            )], **cleared}
        try:
            result = await tool.ainvoke(action.get("args") or {})
        except Exception as e:
            logger.exception("write executor failed: tool=%s", name)
            return {"messages": [AIMessage(
                content=f"Lỗi khi thực hiện thao tác: {e}"
            )], **cleared}
        display, env = parse_write_result(result)
        upd = {"messages": [AIMessage(content=display)],
               "pending_action": None, "confirmed": None,
               "last_write": {"tool": name, **env} if env else None}
        wc = derive_working_context(env)
        if wc:
            # omit-vs-None: chỉ THÊM key khi có đơn mới — không bao giờ set None
            # (None sẽ xoá đơn đang nhớ; các path khác cũng phải OMIT key này).
            upd["working_context"] = wc
        return upd

    return erp_write_executor
