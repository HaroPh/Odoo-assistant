# backend/src/agents/tool_result.py
"""Normalize an MCP/LangChain tool result to plain text. Leaf module (no intra-
package imports) so coordinators can depend on it without import cycles."""

import json


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


def parse_write_result(result) -> tuple[str, dict | None]:
    """(display_text, envelope|None) for a write-tool result.

    The invoice-chain MCP tools return a JSON-string envelope
    {ok, ref, model, res_id, state, display}; everything else returns plain
    text. Envelope is returned only when ok is truthy (ok=false shows its
    display but offers no continuation step)."""
    text = _tool_result_text(result)
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text, None
    if not (isinstance(data, dict) and "ok" in data and "display" in data):
        return text, None
    display = data["display"] or text
    return display, (data if data["ok"] else None)
