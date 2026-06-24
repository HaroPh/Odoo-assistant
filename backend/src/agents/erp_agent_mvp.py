"""
ERP Agent MVP — vertical slice Phase 1.
Ghép: LiteLLM (qwen3:8b) + Odoo MCP tools (SSE) + LangGraph ReAct loop.

Chạy (cần mcp-odoo SSE server đang chạy ở :8001):
    $env:LITELLM_MASTER_KEY = "<key trong .env>"
    python backend/src/agents/erp_agent_mvp.py
"""
import asyncio
import os
from datetime import date

from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent

LITELLM_URL  = os.environ.get("LITELLM_URL", "http://localhost:4000/v1")
LITELLM_KEY  = os.environ["LITELLM_MASTER_KEY"]
MCP_ODOO_URL = os.environ.get("MCP_ODOO_URL", "http://localhost:8001/sse")
MODEL        = os.environ.get("AGENT_MODEL", "qwen3:8b")

SYSTEM_PROMPT = f"""Bạn là trợ lý ERP nội bộ, trả lời bằng tiếng Việt.
Hôm nay là {date.today().isoformat()}.
Khi cần dữ liệu ERP (đơn hàng, tồn kho, khách hàng, nhà cung cấp, doanh thu),
hãy GỌI TOOL phù hợp — không bịa số liệu.
Chỉ trả lời dựa trên kết quả tool. Nếu tool trả về rỗng, nói rõ "không có dữ liệu".
Trả lời ngắn gọn, có số liệu cụ thể. /no_think"""

QUESTIONS = [
    "Sản phẩm nào sắp hết hàng (tồn kho dưới 20)?",   # ép low_stock_threshold → test fix
    "Doanh thu tháng này là bao nhiêu, khách hàng nào mua nhiều nhất?",
]


async def main() -> None:
    llm = ChatOpenAI(model=MODEL, base_url=LITELLM_URL, api_key=LITELLM_KEY,
                     temperature=0, timeout=120)

    client = MultiServerMCPClient(
        {"odoo": {"url": MCP_ODOO_URL, "transport": "sse"}}
    )
    tools = await client.get_tools()
    print(f"✓ Nạp {len(tools)} MCP tools: {[t.name for t in tools]}\n")

    agent = create_agent(llm, tools, system_prompt=SYSTEM_PROMPT)

    for q in QUESTIONS:
        print("=" * 64)
        print("Q:", q)
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": q}]}
        )
        # Đếm tool calls đã thực hiện
        tool_used = [
            tc["name"]
            for m in result["messages"]
            for tc in (getattr(m, "tool_calls", None) or [])
        ]
        print(f"[tools gọi: {tool_used}]")
        print("A:", result["messages"][-1].content.strip())
        print()


if __name__ == "__main__":
    asyncio.run(main())
