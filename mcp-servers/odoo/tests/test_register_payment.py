import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _move(id=64, name="INV/2026/00016", state="posted", payment_state="not_paid",
          amount_residual=640.0, partner=(15, "Azure Interior")):
    return {"id": id, "name": name, "state": state, "payment_state": payment_state,
           "amount_residual": amount_residual, "partner_id": list(partner)}


def _fake(monkeypatch, move_rows, action_ctx=None, wiz_fields=None,
         after_payment_state="in_payment", journal_rows=None):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        if model == "account.move" and method == "search_read":
            return move_rows
        if model == "account.journal" and method == "search":
            return journal_rows if journal_rows is not None else [6]
        if model == "account.move" and method == "action_register_payment":
            return {"context": action_ctx or {"active_model": "account.move.line",
                                              "active_ids": [149, 150]}}
        if model == "account.payment.register" and method == "create":
            return 1
        if model == "account.payment.register" and method == "read":
            return [wiz_fields or {"amount": 640.0, "journal_id": [6, "Bank"]}]
        if model == "account.payment.register" and method == "action_create_payments":
            return None
        if model == "account.move" and method == "read":
            return [{"name": move_rows[0]["name"], "payment_state": after_payment_state}]
        return []

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


def test_invoice_id_not_found(monkeypatch):
    _fake(monkeypatch, [])
    data = _env(fn("register_payment")(invoice_id=999))
    assert data["ok"] is False
    assert "không tìm thấy" in data["display"].lower()


def test_invoice_ref_not_found(monkeypatch):
    _fake(monkeypatch, [])
    data = _env(fn("register_payment")(invoice_ref="INV/2026/99999"))
    assert data["ok"] is False
    assert "không tìm thấy" in data["display"].lower()


def test_invoice_ref_multiple_refused(monkeypatch):
    _fake(monkeypatch, [_move(id=1), _move(id=2)])
    data = _env(fn("register_payment")(invoice_ref="INV/2026/00016"))
    assert data["ok"] is False
    assert "nhiều" in data["display"].lower()


def test_no_selector_given_asks_for_one(monkeypatch):
    _fake(monkeypatch, [])
    data = _env(fn("register_payment")())
    assert data["ok"] is False
    assert "số hóa đơn" in data["display"].lower() or "khách" in data["display"].lower()


def test_draft_invoice_refused(monkeypatch):
    calls = _fake(monkeypatch, [_move(state="draft")])
    data = _env(fn("register_payment")(invoice_id=64))
    assert data["ok"] is False
    assert "chưa phát hành" in data["display"].lower()
    assert not any(c["model"] == "account.payment.register" for c in calls)


def test_already_paid_refused(monkeypatch):
    calls = _fake(monkeypatch, [_move(payment_state="paid")])
    data = _env(fn("register_payment")(invoice_id=64))
    assert data["ok"] is False
    assert "đã thanh toán đủ" in data["display"].lower()
    assert not any(c["model"] == "account.payment.register" for c in calls)


def test_reversed_refused(monkeypatch):
    calls = _fake(monkeypatch, [_move(payment_state="reversed")])
    data = _env(fn("register_payment")(invoice_id=64))
    assert data["ok"] is False
    assert "đảo" in data["display"].lower()
    assert not any(c["model"] == "account.payment.register" for c in calls)


def test_partial_payment_state_allowed(monkeypatch):
    _fake(monkeypatch, [_move(payment_state="partial", amount_residual=200.0)],
         wiz_fields={"amount": 200.0, "journal_id": [6, "Bank"]},
         after_payment_state="paid")
    data = _env(fn("register_payment")(invoice_id=64))
    assert data["ok"] is True


def test_happy_path_via_invoice_id(monkeypatch):
    calls = _fake(monkeypatch, [_move()])
    data = _env(fn("register_payment")(invoice_id=64))
    assert data["ok"] is True
    assert data["ref"] == "INV/2026/00016"
    assert data["model"] == "account.move" and data["res_id"] == 64
    assert data["state"] == "in_payment"
    assert "640" in data["display"] and "Azure Interior" in data["display"]
    wiz_create = next(c for c in calls if c["model"] == "account.payment.register"
                      and c["method"] == "create")
    assert wiz_create["kwargs"]["context"]["active_ids"] == [149, 150]
    commit = next(c for c in calls if c["method"] == "action_create_payments")
    assert commit["args"] == [[1]]


def test_uses_action_register_payment_context_verbatim_not_hand_built(monkeypatch):
    # Regression guard for the real Odoo probe finding: active_model must be
    # "account.move.line" with the REAL line ids the wizard computed — never a
    # hand-constructed {"active_model": "account.move", "active_ids": [move_id]}.
    calls = _fake(monkeypatch, [_move()],
                 action_ctx={"active_model": "account.move.line",
                            "active_ids": [777, 778]})
    fn("register_payment")(invoice_id=64)
    wiz_create = next(c for c in calls if c["model"] == "account.payment.register"
                      and c["method"] == "create")
    assert wiz_create["kwargs"]["context"] == {"active_model": "account.move.line",
                                               "active_ids": [777, 778]}


def test_happy_path_via_invoice_ref(monkeypatch):
    _fake(monkeypatch, [_move()])
    data = _env(fn("register_payment")(invoice_ref="INV/2026/00016"))
    assert data["ok"] is True
    assert data["ref"] == "INV/2026/00016"


def test_happy_path_via_partner_name(monkeypatch):
    _fake(monkeypatch, [_move()])
    data = _env(fn("register_payment")(partner_name="Azure Interior"))
    assert data["ok"] is True


def test_partner_name_multiple_matches_refused(monkeypatch):
    calls = _fake(monkeypatch, [_move(id=1), _move(id=2)])
    data = _env(fn("register_payment")(partner_name="Azure Interior"))
    assert data["ok"] is False
    assert "nhiều" in data["display"].lower()
    assert not any(c["model"] == "account.payment.register" for c in calls)


def test_invalid_journal_value_refused(monkeypatch):
    calls = _fake(monkeypatch, [_move()])
    data = _env(fn("register_payment")(invoice_id=64, journal="crypto"))
    assert data["ok"] is False
    assert "không hợp lệ" in data["display"].lower()
    assert not any(c["model"] == "account.payment.register" for c in calls)


def test_journal_resolved_by_type_passed_to_wizard(monkeypatch):
    calls = _fake(monkeypatch, [_move()], journal_rows=[13])
    fn("register_payment")(invoice_id=64, journal="cash")
    jsearch = next(c for c in calls if c["model"] == "account.journal")
    assert jsearch["args"] == [[["type", "=", "cash"]]]
    wiz_create = next(c for c in calls if c["model"] == "account.payment.register"
                      and c["method"] == "create")
    assert wiz_create["args"] == [{"journal_id": 13}]


def test_journal_type_not_found(monkeypatch):
    calls = _fake(monkeypatch, [_move()], journal_rows=[])
    data = _env(fn("register_payment")(invoice_id=64, journal="cash"))
    assert data["ok"] is False
    assert not any(c["model"] == "account.payment.register" for c in calls)


def test_new_methods_in_operation_map():
    assert server.ODOO_METHOD_OPERATION_MAP.get("action_register_payment") == "write"
    assert server.ODOO_METHOD_OPERATION_MAP.get("action_create_payments") == "write"
