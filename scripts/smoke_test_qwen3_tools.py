"""
⭐ SMOKE TEST — validate giả định rủi ro nhất của Phase 1:
   Qwen3:8b có gọi tool ĐÚNG với prompt tiếng Việt qua LiteLLM gateway không?

Chạy SAU khi: docker compose up -d postgres ollama litellm
              docker exec ollama ollama pull qwen3:8b

    # từ .venv:
    python scripts/smoke_test_qwen3_tools.py

Tiêu chí PASS: model trả về tool_call `search_late_orders` thay vì văn bản thường.
Nếu FAIL → cân nhắc qwen2.5:7b fallback hoặc chỉnh prompt/template TRƯỚC khi viết Agent.
"""
import json
import os
import sys

import httpx  # đã có trong .venv

# Windows console mặc định cp1252 → ép UTF-8 để in được →/✓/✗
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

GATEWAY = os.environ.get("LITELLM_URL", "http://localhost:4000/v1/chat/completions")
API_KEY = os.environ.get("LITELLM_MASTER_KEY", "sk-change_me_master_key")
MODEL = os.environ.get("SMOKE_MODEL", "qwen3:8b")

TOOLS = [{
    "type": "function",
    "function": {
        "name": "search_late_orders",
        "description": "Tìm các đơn hàng đang giao trễ trong Odoo",
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "limit": {"type": "integer", "default": 50},
            },
        },
    },
}]

# /no_think: tắt thinking-mode của Qwen3 để giảm latency + tránh <think> lẫn output
SYSTEM = "Bạn là trợ lý ERP. Khi cần dữ liệu Odoo, hãy gọi tool phù hợp. /no_think"
USER = "Đơn hàng nào đang trễ trong tháng này?"


def main() -> int:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER},
        ],
        "tools": TOOLS,
        "tool_choice": "auto",
        "temperature": 0,
    }
    print(f"→ POST {GATEWAY}  model={MODEL}")
    try:
        r = httpx.post(
            GATEWAY,
            headers={"Authorization": f"Bearer {API_KEY}"},
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        print(f"✗ Lỗi gọi gateway: {e}")
        print("  Kiểm tra: litellm container up? đã `ollama pull qwen3:8b`? LITELLM_MASTER_KEY đúng?")
        return 2

    msg = r.json()["choices"][0]["message"]
    tool_calls = msg.get("tool_calls") or []

    if tool_calls:
        tc = tool_calls[0]["function"]
        print(f"✓ PASS — model gọi tool: {tc['name']}")
        print(f"  arguments: {tc.get('arguments')}")
        try:
            json.loads(tc.get("arguments") or "{}")
            print("  ✓ arguments là JSON hợp lệ")
        except json.JSONDecodeError:
            print("  ⚠ arguments KHÔNG phải JSON hợp lệ — cần validation layer")
        return 0

    print("✗ FAIL — model trả văn bản, KHÔNG gọi tool:")
    print(f"  {(msg.get('content') or '')[:400]}")
    print("  → Thử qwen2.5:7b (SMOKE_MODEL=qwen2.5:7b) hoặc chỉnh system prompt.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
