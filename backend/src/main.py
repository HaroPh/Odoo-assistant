"""
FastAPI backend — OpenAI-compatible API bọc ERP agent.
Open WebUI nối vào endpoint /v1 này như một "model" (erp-assistant).

Chạy (host, cần mcp-odoo SSE :8001 + litellm :4000 đang chạy):
    cd backend
    uvicorn src.main:app --host 0.0.0.0 --port 8000
"""
import hashlib
import json
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.agents.erp_agent import ERPAgent

MODEL_ID = "erp-assistant"
_state: dict = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    agent = ERPAgent()
    await agent.setup()
    _state["agent"] = agent
    print(f"✓ ERP agent ready — tools: {agent.tool_names}")
    yield
    agent = _state.get("agent")
    if agent is not None:
        await agent.aclose()
    _state.clear()


app = FastAPI(title="ERP AI Assistant Backend", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"status": "ok", "agent_ready": "agent" in _state}


@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": [
        {"id": MODEL_ID, "object": "model",
         "created": int(time.time()), "owned_by": "erp-ai"},
    ]}


def _filter_messages(messages: list[dict]) -> list[dict]:
    """Bỏ system (đã có baked prompt), giữ user/assistant để multi-turn."""
    return [{"role": m["role"], "content": m["content"]}
            for m in messages
            if m.get("role") in ("user", "assistant") and m.get("content")]


def _derive_thread_id(body: dict, messages: list[dict], headers=None) -> str | None:
    """Stable per-conversation thread for interrupt/resume.

    Priority (R7 fix, spec 2026-07-09-r7-thread-scoping):
      1. Open WebUI identity headers — real per-chat id, sent when the
         open-webui container has ENABLE_FORWARD_USER_INFO_HEADERS=true.
         Only the two id headers are read; name/email/role are PII and must
         never be read or logged.
      2. Explicit id from the client body (scripts/curl).
      3. Hash of the FIRST user message — stable across the turns of one
         conversation, but collides across conversations with identical
         openers; the fresh-conversation reset in ERPAgent.chat mitigates.
      4. None (no user message).
    """
    if headers is not None:
        chat_id = headers.get("x-openwebui-chat-id")
        if chat_id:
            user_id = headers.get("x-openwebui-user-id") or "anon"
            return f"owui:{user_id}:{chat_id}"
    explicit = body.get("session_id") or body.get("id")
    if explicit:
        return str(explicit)
    first_user = next((m["content"] for m in messages if m.get("role") == "user"), "")
    if not first_user:
        return None
    return "conv-" + hashlib.sha1(first_user.encode("utf-8")).hexdigest()[:16]


@app.post("/v1/chat/completions")
async def chat_completions(req: Request):
    body = await req.json()
    stream = bool(body.get("stream", False))
    messages = _filter_messages(body.get("messages", []))

    agent: ERPAgent = _state["agent"]
    # Stable thread per conversation so multi-turn confirmation resumes correctly
    # (Open WebUI sends no session id — derive one from the first user message).
    thread_id = _derive_thread_id(body, messages, headers=req.headers)
    answer = await agent.chat(messages, thread_id=thread_id)

    cid = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    created = int(time.time())

    if not stream:
        return JSONResponse({
            "id": cid, "object": "chat.completion", "created": created, "model": MODEL_ID,
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": answer},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    # Streaming: agent trả nguyên câu → emit 1 content chunk + [DONE] (đủ cho Open WebUI)
    async def sse():
        base = {"id": cid, "object": "chat.completion.chunk",
                "created": created, "model": MODEL_ID}
        yield f'data: {json.dumps({**base, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})}\n\n'
        yield f'data: {json.dumps({**base, "choices": [{"index": 0, "delta": {"content": answer}, "finish_reason": None}]}, ensure_ascii=False)}\n\n'
        yield f'data: {json.dumps({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n'
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")
