"""Eval observe-only cho resolve_entity trên bộ cases 'messy variants' —
4 failure class đo được + control (spec 2026-07-13 §12, adopt từ ERPGulf ở
dạng eval, không phải training). Dev tool như rag/eval: cần dev Odoo đã seed
(scripts/seed_vn_products.py) + index đã sync. Không gate CI.

Chạy A/B (CWD backend/):
  ERP_SEMANTIC_RESOLVE=0 python -m src.erp_query.eval_resolve   # baseline
  ERP_SEMANTIC_RESOLVE=1 python -m src.erp_query.eval_resolve
"""
import json
import os
from collections import defaultdict

from .gateway import default_gateway
from .resolve import resolve_entity

CASES = os.path.join(os.path.dirname(__file__), "eval_resolve_cases.json")


def _expected_ids(gw, codes: set[str]) -> dict:
    rows = gw.search_read("product.product",
                          [["default_code", "in", sorted(codes)]],
                          ["default_code"])
    return {r["default_code"]: r["id"] for r in rows}


def evaluate(gw=None) -> dict:
    gw = gw or default_gateway()
    with open(CASES, encoding="utf-8") as f:
        cases = json.load(f)
    code_to_id = _expected_ids(gw, {c["expect_code"] for c in cases})
    stats = defaultdict(lambda: {"n": 0, "top1": 0, "in_cands": 0})
    top1_scores, missing = [], []
    for case in cases:
        exp = code_to_id.get(case["expect_code"])
        if exp is None:
            missing.append(case["expect_code"])
            continue
        out = resolve_entity("product.product", case["q"], gw=gw)
        matches = (out.get("data") or {}).get("matches", []) \
            if out["status"] == "success" else []
        s = stats[case["class"]]
        s["n"] += 1
        if matches and matches[0]["id"] == exp:
            s["top1"] += 1
        if any(m["id"] == exp for m in matches):
            s["in_cands"] += 1
        if matches:
            top1_scores.append(matches[0]["score"])
    total = {"n": 0, "top1": 0, "in_cands": 0}
    for s in stats.values():
        for key in total:
            total[key] += s[key]
    return {"by_class": dict(stats), "total": total,
            "top1_scores": top1_scores, "missing_codes": sorted(set(missing))}


if __name__ == "__main__":
    r = evaluate()
    if r["missing_codes"]:
        print(f"Cảnh báo: chưa seed {r['missing_codes']} — chạy "
              "scripts/seed_vn_products.py trước.")
    for cls, s in sorted(r["by_class"].items()):
        print(f"{cls:12} n={s['n']:3} top1={s['top1'] / s['n']:.2f} "
              f"in_cands={s['in_cands'] / s['n']:.2f}")
    t = r["total"]
    if t["n"]:
        print(f"{'TONG':12} n={t['n']:3} top1={t['top1'] / t['n']:.2f} "
              f"in_cands={t['in_cands'] / t['n']:.2f}")
        sc = sorted(r["top1_scores"])
        pct = {p: round(sc[min(len(sc) - 1, int(len(sc) * p / 100))], 3)
               for p in (10, 25, 50, 75, 90)} if sc else {}
        print("top1 score percentiles (calibrate FLOOR/GAP):", pct)
