import os
import yaml

from ..retrieve import retrieve

HERE = os.path.dirname(__file__)
EVAL_SET = os.path.join(HERE, "eval_set.yaml")


def evaluate(conn, k: int = 6) -> dict:
    with open(EVAL_SET, encoding="utf-8") as f:
        cases = yaml.safe_load(f)
    hits, misses = 0, []
    for case in cases:
        res = retrieve(case["q"], k=k, conn=conn)
        files = [os.path.basename(c.source_file) for c in res.chunks]
        ok = case["expect_file"] in files
        if ok and case.get("expect_section"):
            ok = any(case["expect_section"] in (c.section_path or "")
                     for c in res.chunks
                     if os.path.basename(c.source_file) == case["expect_file"])
        hits += int(ok)
        if not ok:
            misses.append(case["q"])
    n = len(cases)
    return {"recall_at_k": hits / n if n else 0.0, "n": n, "misses": misses}
