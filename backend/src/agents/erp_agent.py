"""
ERP Agent — reusable cho FastAPI backend.
Build 1 lần lúc startup (lifespan), tái dùng cho mọi request.
"""
import os
from datetime import date

from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain.agents import create_agent

LITELLM_URL  = os.environ.get("LITELLM_URL", "http://localhost:4000/v1")
LITELLM_KEY  = os.environ.get("LITELLM_MASTER_KEY", "")
MCP_ODOO_URL = os.environ.get("MCP_ODOO_URL", "http://localhost:8001/sse")
MODEL        = os.environ.get("AGENT_MODEL", "qwen3:8b")

SYSTEM_PROMPT = f"""Bạn là trợ lý ERP nội bộ, trả lời bằng tiếng Việt.
Hôm nay là {date.today().isoformat()}.
Khi cần dữ liệu ERP (đơn hàng, tồn kho, khách hàng, nhà cung cấp, doanh thu),
hãy GỌI TOOL phù hợp — không bịa số liệu.
Chỉ trả lời dựa trên kết quả tool. Nếu tool trả về rỗng, nói rõ "không có dữ liệu".
Trả lời ngắn gọn, có số liệu cụ thể. /no_think"""


class ERPAgent:
    def __init__(self) -> None:
        self.agent = None
        self.tool_names: list[str] = []

    async def setup(self) -> None:
        llm = ChatOpenAI(model=MODEL, base_url=LITELLM_URL, api_key=LITELLM_KEY,
                         temperature=0, timeout=120)
        client = MultiServerMCPClient(
            {"odoo": {"url": MCP_ODOO_URL, "transport": "sse"}}
        )
        tools = await client.get_tools()
        self.tool_names = [t.name for t in tools]
        self.agent = create_agent(llm, tools, system_prompt=SYSTEM_PROMPT)

    async def chat(self, messages: list[dict]) -> str:
        """messages: list {"role","content"} (user/assistant) — hỗ trợ multi-turn."""
        if not messages:
            return "Vui lòng nhập câu hỏi."
        result = await self.agent.ainvoke({"messages": messages})
        return result["messages"][-1].content.strip()
