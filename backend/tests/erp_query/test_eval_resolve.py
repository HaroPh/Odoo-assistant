import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query import eval_resolve


class FakeGw:
    def search_read(self, model, domain, fields, order=None, limit=50, context=None):
        return [{"id": 9, "default_code": "VN-GHE-01"},
                {"id": 7, "default_code": "VN-DEN-01"}]


def test_evaluate_scores_top1_in_cands_and_missing(monkeypatch, tmp_path):
    cases = [
        {"q": "ghe xoay", "expect_code": "VN-GHE-01", "class": "khong_dau"},
        {"q": "den hoc", "expect_code": "VN-DEN-01", "class": "dong_nghia"},
        {"q": "x", "expect_code": "VN-CHUA-SEED", "class": "typo"},
    ]
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(cases), encoding="utf-8")
    monkeypatch.setattr(eval_resolve, "CASES", str(p))

    def fake_resolve(model, query, *, gw=None):
        if query == "ghe xoay":    # top1 đúng
            return {"status": "success", "data": {"matches": [
                {"id": 9, "name": "Ghế xoay", "score": 0.9}],
                "needs_disambiguation": False}}
        return {"status": "success", "data": {"matches": [   # đúng ở vị trí 2
            {"id": 5, "name": "Đèn chùm", "score": 0.6},
            {"id": 7, "name": "Đèn bàn LED", "score": 0.5}],
            "needs_disambiguation": True}}
    monkeypatch.setattr(eval_resolve, "resolve_entity", fake_resolve)

    r = eval_resolve.evaluate(gw=FakeGw())
    assert r["by_class"]["khong_dau"] == {"n": 1, "top1": 1, "in_cands": 1}
    assert r["by_class"]["dong_nghia"] == {"n": 1, "top1": 0, "in_cands": 1}
    assert r["total"] == {"n": 2, "top1": 1, "in_cands": 2}
    assert r["missing_codes"] == ["VN-CHUA-SEED"]
    assert r["top1_scores"] == [0.9, 0.6]
