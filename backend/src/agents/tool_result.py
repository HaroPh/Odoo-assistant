# backend/src/agents/tool_result.py
"""Normalize an MCP/LangChain tool result to plain text. Leaf module (no intra-
package imports) so coordinators can depend on it without import cycles."""


def _tool_result_text(result) -> str:
    """langchain MCP tools return a list of content-block dicts
    (e.g. [{"type": "text", "text": "..."}]) rather than a bare string;
    surface the joined text, not its repr."""
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts = [b.get("text", "") if isinstance(b, dict) else str(b)
                 for b in result]
        joined = "".join(parts).strip()
        return joined or str(result)
    return str(result)
