import os
import yaml

from ..retrieve import retrieve

HERE = os.path.dirname(__file__)
EVAL_SET = os.path.join(HERE, "eval_set.yaml")


def _first_match(chunks, case) -> int | None:
    """Index (0-based) của chunk ĐẦU TIÊN khớp expect_file — và nếu case có
    expect_section thì section phải khớp trên CHÍNH chunk đó. None = miss.
    Cùng predicate với recall cũ (file-match, section-match trong file đó)
    nhưng trả vị trí để tính MRR (spec 2026-07-12-rag-reranker §3.5)."""
    for i, c in enumerate(chunks):
        if os.path.basename(c.source_file) != case["expect_file"]:
            continue
        if case.get("expect_section") and \
                case["expect_section"] not in (c.section_path or ""):
            continue
        return i
    return None


def evaluate(conn, k: int = 6) -> dict:
    with open(EVAL_SET, encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    hits, misses, rr_sum = 0, [], 0.0
    for case in cases:
        res = retrieve(case["q"], k=k, conn=conn)
        idx = _first_match(res.chunks, case)
        if idx is not None:
            hits += 1
            rr_sum += 1.0 / (idx + 1)
        else:
            misses.append(case["q"])
    n = len(cases)
    return {"recall_at_k": hits / n if n else 0.0,
            "mrr": rr_sum / n if n else 0.0,
            "n": n, "misses": misses}
