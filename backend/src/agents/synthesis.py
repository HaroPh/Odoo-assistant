# backend/src/agents/synthesis.py
"""Doc-answering synthesis (agents layer).

Turns S1 retrieval results into a grounded answer + a deterministic citation
footer, and owns the no-result guard. This keeps backend/src/rag/ synthesis-free
— all answer/refuse/threshold logic lives here, not in the retrieval library.
"""
import os
import re

from langchain_core.messages import SystemMessage, HumanMessage

from .prompts import RAG_SYNTHESIS_PROMPT, CITATION_VERIFY_PROMPT

COS_FLOOR = float(os.environ.get("RAG_NO_RESULT_COS_FLOOR", "0.35"))
SENTINEL = "KHÔNG_ĐỦ_THÔNG_TIN"
GUARD_MSG = "Không tìm thấy tài liệu liên quan đến câu hỏi này."
SAFE_MSG = "Xin lỗi, tính năng tra cứu tài liệu tạm thời gặp sự cố. Vui lòng thử lại sau."
USED_MARKER = "NGUỒN_DÙNG"
# Marker is contractually the LAST line of the answer — extract_used_citations()
# discards everything from the match onward, so any trailing text after it
# (there shouldn't be any) is dropped along with the marker itself.
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


def extract_used_citations(body: str, chunks: list) -> tuple[str, list]:
    """Strip the LLM's NGUỒN_DÙNG marker line and resolve which chunks it
    names. Falls back to all chunks if the marker is missing or names no
    valid chunk index. Caller verifies (verify_citations) and builds the
    citation footer (build_citations) from the returned list."""
    m = _MARKER_RE.search(body)
    if not m:
        return body, chunks
    clean = body[:m.start()].rstrip()
    indices = {int(x) for x in re.findall(r'\d+', m.group(1))}
    used = [c for i, c in enumerate(chunks, start=1) if i in indices]
    if not used:
        return clean, chunks
    return clean, used


async def verify_citations(answer: str, chunks: list, llm) -> list:
    """Xác minh lại các chunk được đánh dấu đã dùng bằng 1 lệnh gọi LLM,
    đối chiếu với nội dung THẬT của từng chunk — không chỉ tin lời tự khai
    của marker NGUỒN_DÙNG. Fail-open toàn phần (lỗi/timeout → giữ nguyên
    chunks) và từng dòng (verdict thiếu/không parse được → giữ, chỉ loại
    khi có KHÔNG tường minh)."""
    if not chunks:
        return chunks
    try:
        resp = await llm.ainvoke([
            SystemMessage(content=CITATION_VERIFY_PROMPT),
            HumanMessage(content=(
                f"CÂU TRẢ LỜI:\n{answer}\n\nCÁC ĐOẠN TÀI LIỆU:\n"
                + _format_context(chunks))),
        ])
        verdicts = dict(re.findall(r'(\d+):\s*(CÓ|KHÔNG)', resp.content or "",
                                   re.IGNORECASE))
        return [c for i, c in enumerate(chunks, start=1)
                if verdicts.get(str(i), "").upper() != "KHÔNG"]
    except Exception:
        return chunks


async def cite_and_verify(body: str, chunks: list, llm) -> str:
    """Full citation pipeline shared by synthesize() and fusion_node:
    resolve which chunks the marker claims were used (extract_used_citations),
    verify that claim against real chunk content (verify_citations), then
    build the footer from whatever survives (build_citations)."""
    clean, used = extract_used_citations(body, chunks)
    verified = await verify_citations(clean, used, llm)
    return clean + build_citations(verified)


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
    retrieval) backed by the LLM answerability sentinel. Citations are
    verified against real chunk content (verify_citations) before the
    footer is built, not just trusted from the LLM's marker self-report.
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
    return await cite_and_verify(body, result.chunks, llm)
