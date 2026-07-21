# backend/src/agents/synthesis.py
"""Doc-answering synthesis (agents layer).

Turns S1 retrieval results into a grounded answer + a deterministic citation
footer, and owns the no-result guard. This keeps backend/src/rag/ synthesis-free
— all answer/refuse/threshold logic lives here, not in the retrieval library.
"""
import os
import re

from langchain_core.messages import SystemMessage, HumanMessage

from .prompts import RAG_SYNTHESIS_PROMPT

COS_FLOOR = float(os.environ.get("RAG_NO_RESULT_COS_FLOOR", "0.35"))
SENTINEL = "KHÔNG_ĐỦ_THÔNG_TIN"
GUARD_MSG = "Không tìm thấy tài liệu liên quan đến câu hỏi này."
SAFE_MSG = "Xin lỗi, tính năng tra cứu tài liệu tạm thời gặp sự cố. Vui lòng thử lại sau."
USED_MARKER = "NGUỒN_DÙNG"
_MARKER_RE = re.compile(rf'\n?{USED_MARKER}:\s*([0-9,\s]*)', re.IGNORECASE)


def build_citations(chunks) -> str:
    """Deterministic '📄 Nguồn:' footer from chunk metadata.

    Deduped by (source_file, section_path or sheet), retrieval order preserved.
    Text chunk → "{section_path} ({file}, tr.{page})" (page omitted if None).
    xlsx chunk → "{sheet} ({file}, {row_range})". Empty list → "".
    """
    if not chunks:
        return ""
    lines: list[str] = []
    seen: set = set()
    for c in chunks:
        key = (c.source_file, c.section_path or c.sheet)
        if key in seen:
            continue
        seen.add(key)
        base = os.path.basename(c.source_file)
        if c.sheet:
            lines.append(f"• {c.sheet} ({base}, {c.row_range})")
        else:
            loc = c.section_path or base
            tail = f", tr.{c.page}" if c.page is not None else ""
            lines.append(f"• {loc} ({base}{tail})")
    return "\n\n📄 Nguồn:\n" + "\n".join(lines)


def extract_used_citations(body: str, chunks: list) -> tuple[str, str]:
    """Strip the LLM's NGUỒN_DÙNG marker line and build a citation footer
    limited to the chunks it names. Falls back to citing all chunks if the
    marker is missing or names no valid chunk index."""
    m = _MARKER_RE.search(body)
    if not m:
        return body, build_citations(chunks)
    clean = body[:m.start()].rstrip()
    indices = {int(x) for x in re.findall(r'\d+', m.group(1))}
    used = [c for i, c in enumerate(chunks, start=1) if i in indices]
    if not used:
        return clean, build_citations(chunks)
    return clean, build_citations(used)


def _format_context(chunks, start: int = 1) -> str:
    """Numbered chunk texts, each tagged with its source label, for the prompt."""
    parts = []
    for i, c in enumerate(chunks, start=start):
        label = c.section_path or c.sheet or os.path.basename(c.source_file)
        parts.append(f"[{i}] ({label}) {c.text}")
    return "\n".join(parts)


def passes_floor(result) -> bool:
    """Cheap no-result pre-filter shared by doc-only synthesis and fusion.

    True if any chunk clears the cosine floor (COS_FLOOR) or has any sparse
    (FTS) hit. Skips the LLM on an obviously-empty/off-topic retrieval; a
    keyword (FTS) hit always counts.
    """
    return (
        any(c.dense_score is not None and c.dense_score >= COS_FLOOR
            for c in result.chunks)
        or any(c.sparse_score is not None for c in result.chunks)
    )


async def synthesize(query: str, result, llm) -> str:
    """Grounded answer + citation footer, or GUARD_MSG when nothing answers.

    Guard = cheap cosine pre-filter (no LLM on an obviously-empty/off-topic
    retrieval) backed by the LLM answerability sentinel.
    """
    if result.is_empty() or not passes_floor(result):
        return GUARD_MSG
    resp = await llm.ainvoke([
        SystemMessage(content=RAG_SYNTHESIS_PROMPT),
        HumanMessage(content=f"TÀI LIỆU:\n{_format_context(result.chunks)}\n\nCÂU HỎI: {query}"),
    ])
    body = (resp.content or "").strip()
    if SENTINEL in body:
        return GUARD_MSG
    clean, footer = extract_used_citations(body, result.chunks)
    return clean + footer
