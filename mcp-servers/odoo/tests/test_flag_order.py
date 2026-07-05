import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _patch(monkeypatch, rows, post_capture=None, search_raises=None):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        if method == "search_read":
            if search_raises:
                raise search_raises
            return rows
        if method == "message_post":
            if post_capture is not None:
                post_capture.append((model, args, kwargs))
            return 999  # a mail.message id
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


def test_flag_rejects_disallowed_model(monkeypatch):
    posts = []
    calls = _patch(monkeypatch, rows=[{"id": 1, "name": "X", "state": "sale"}], post_capture=posts)
    data = _env(fn("flag_order_for_review")("res.partner", "S001", "hack"))
    assert data["ok"] is False
    assert calls == []  # no Odoo calls of any kind for disallowed model
    assert posts == []


def test_flag_not_found(monkeypatch):
    _patch(monkeypatch, rows=[], post_capture=[])
    data = _env(fn("flag_order_for_review")("sale.order", "S99999", "note"))
    assert data["ok"] is False
    assert "không tìm thấy" in data["display"].lower()


def test_flag_ambiguous(monkeypatch):
    posts = []
    _patch(monkeypatch, rows=[{"id": 1, "name": "S001", "state": "sale"},
                              {"id": 2, "name": "S001", "state": "sale"}],
           post_capture=posts)
    data = _env(fn("flag_order_for_review")("sale.order", "S001", "note"))
    assert data["ok"] is False
    assert "nhiều" in data["display"].lower()
    assert posts == []  # never touched Odoo


def test_flag_posts_note_and_returns_envelope(monkeypatch):
    posts = []
    _patch(monkeypatch, rows=[{"id": 5, "name": "S00012", "state": "sale"}], post_capture=posts)
    data = _env(fn("flag_order_for_review")("sale.order", "S00012", "Đề nghị sửa: thêm Tủ × 2."))
    assert data["ok"] is True
    assert data["ref"] == "S00012"
    assert data["model"] == "sale.order"
    assert data["res_id"] == 5
    assert data["state"] == "sale"
    assert "S00012" in data["display"]
    model, args, kwargs = posts[0]
    assert model == "sale.order" and args == [[5]]
    assert kwargs["body"] == "Đề nghị sửa: thêm Tủ × 2."


def test_flag_purchase_order_posts_note(monkeypatch):
    posts = []
    _patch(monkeypatch, rows=[{"id": 7, "name": "P00003", "state": "purchase"}], post_capture=posts)
    data = _env(fn("flag_order_for_review")("purchase.order", "P00003", "Cần duyệt thêm."))
    assert data["ok"] is True
    assert data["ref"] == "P00003"
    assert data["model"] == "purchase.order"
    assert data["res_id"] == 7
    assert data["state"] == "purchase"
    model, args, kwargs = posts[0]
    assert model == "purchase.order" and args == [[7]]
    assert kwargs["body"] == "Cần duyệt thêm."


def test_flag_exception_becomes_friendly_error(monkeypatch):
    _patch(monkeypatch, rows=[], search_raises=ValueError("boom"))
    data = _env(fn("flag_order_for_review")("sale.order", "S00012", "note"))
    assert data["ok"] is False
    assert "lỗi khi ghi chú đơn" in data["display"].lower() and "boom" in data["display"]
