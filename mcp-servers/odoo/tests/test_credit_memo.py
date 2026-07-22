import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _fake(monkeypatch, responses):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        v = responses.get((model, method), [])
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


_POSTED_INVOICE = {"id": 68, "name": "INV/2026/00017", "state": "posted",
                   "partner_id": [15, "Azure Interior"], "journal_id": [1, "Sales"]}


def _base(monkeypatch, extra=None):
    responses = {
        ("account.move", "search_read"): [_POSTED_INVOICE],
        ("account.move.reversal", "create"): 2,
        ("account.move.reversal", "refund_moves"): {"res_id": 72},
        ("account.move.reversal", "read"): [{"new_move_ids": [72]}],
        ("account.move", "read"): [{"state": "draft", "amount_total": 70.0}],
    }
    if extra:
        responses.update(extra)
    return _fake(monkeypatch, responses)


def test_create_credit_memo_invoice_not_found(monkeypatch):
    _fake(monkeypatch, {("account.move", "search_read"): []})
    out = _env(fn("create_credit_memo")(999))
    assert out["ok"] is False and "Không tìm thấy" in out["display"]


def test_create_credit_memo_not_posted(monkeypatch):
    _fake(monkeypatch, {("account.move", "search_read"): [
        {"id": 68, "name": "INV/2026/00099", "state": "draft",
         "partner_id": [15, "Azure Interior"], "journal_id": [1, "Sales"]}]})
    out = _env(fn("create_credit_memo")(68))
    assert out["ok"] is False and "chưa phát hành" in out["display"]


def test_create_credit_memo_passes_journal_id_explicitly(monkeypatch):
    calls = _base(monkeypatch)
    out = _env(fn("create_credit_memo")(68))
    assert out["ok"] is True
    assert out["ref"] is None and out["model"] == "account.move"
    assert out["res_id"] == 72 and out["state"] == "draft"
    create = next(c for c in calls if c["model"] == "account.move.reversal"
                  and c["method"] == "create")
    vals = create["args"][0]
    assert vals["move_ids"] == [(6, 0, [68])]
    assert vals["journal_id"] == 1
    assert "reason" not in vals


def test_create_credit_memo_reason_included_when_given(monkeypatch):
    calls = _base(monkeypatch)
    fn("create_credit_memo")(68, reason="Hàng lỗi")
    create = next(c for c in calls if c["model"] == "account.move.reversal"
                  and c["method"] == "create")
    assert create["args"][0]["reason"] == "Hàng lỗi"


def test_create_credit_memo_no_new_move_reported_safely(monkeypatch):
    _base(monkeypatch, extra={
        ("account.move.reversal", "read"): [{"new_move_ids": []}]})
    out = _env(fn("create_credit_memo")(68))
    assert out["ok"] is False and "kiểm tra trên Odoo" in out["display"]


def test_create_credit_memo_odoo_fault(monkeypatch):
    _fake(monkeypatch, {
        ("account.move", "search_read"): RuntimeError("Odoo down")})
    out = _env(fn("create_credit_memo")(68))
    assert out["ok"] is False and "Odoo down" in out["display"]
