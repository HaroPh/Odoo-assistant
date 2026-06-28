import server


def patch_odoo(monkeypatch, by_model, confirm_capture=None):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method, "args": args})
        if confirm_capture is not None and method in (
            "button_confirm", "action_post", "button_validate", "create",
        ):
            confirm_capture.append((model, method, args))
            return True
        return by_model.get((model, method), by_model.get(model, []))

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


# ── confirm_purchase_order ────────────────────────────────────────────────────

def test_confirm_po_not_found(monkeypatch):
    patch_odoo(monkeypatch, {"purchase.order": []})
    out = fn("confirm_purchase_order")("P99999")
    assert "không tìm thấy" in out.lower()


def test_confirm_po_ambiguous(monkeypatch):
    patch_odoo(monkeypatch, {"purchase.order": [
        {"id": 1, "name": "P001", "state": "draft"},
        {"id": 2, "name": "P001", "state": "draft"},
    ]})
    assert "nhiều" in fn("confirm_purchase_order")("P001").lower()


def test_confirm_po_already_confirmed_idempotent(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"purchase.order": [{"id": 3, "name": "P00003", "state": "purchase"}]},
               confirm_capture=cap)
    out = fn("confirm_purchase_order")("P00003")
    assert "đã được xác nhận" in out.lower()
    assert cap == []  # button_confirm NOT called


def test_confirm_po_cancelled(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"purchase.order": [{"id": 4, "name": "P00004", "state": "cancel"}]},
               confirm_capture=cap)
    out = fn("confirm_purchase_order")("P00004")
    assert "hủy" in out.lower()
    assert cap == []


def test_confirm_po_draft_calls_button_confirm(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"purchase.order": [{"id": 5, "name": "P00005", "state": "draft"}]},
               confirm_capture=cap)
    out = fn("confirm_purchase_order")("P00005")
    assert "đã xác nhận" in out.lower()
    assert ("purchase.order", "button_confirm", [[5]]) in cap


def test_confirm_po_sent_calls_button_confirm(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"purchase.order": [{"id": 6, "name": "P00006", "state": "sent"}]},
               confirm_capture=cap)
    out = fn("confirm_purchase_order")("P00006")
    assert "đã xác nhận" in out.lower()
    assert ("purchase.order", "button_confirm", [[6]]) in cap


# ── post_invoice ──────────────────────────────────────────────────────────────

def test_post_invoice_no_draft(monkeypatch):
    patch_odoo(monkeypatch, {("account.move", "search_read"): []})
    assert "không tìm thấy" in fn("post_invoice")("Nobody").lower()


def test_post_invoice_filters_draft_and_partner(monkeypatch):
    calls = patch_odoo(monkeypatch, {("account.move", "search_read"): []})
    fn("post_invoice")("Azure Interior")
    domain = calls[0]["args"][0]
    assert ["state", "=", "draft"] in domain
    assert ["partner_id.name", "ilike", "Azure Interior"] in domain
    assert ["move_type", "in", ["out_invoice", "in_invoice"]] in domain


def test_post_invoice_multiple_drafts_lists_candidates(monkeypatch):
    cap = []
    patch_odoo(monkeypatch, {("account.move", "search_read"): [
        {"id": 1, "partner_id": [2, "Azure Interior"], "amount_total": 100.0,
         "invoice_date": "2026-06-27", "move_type": "out_invoice"},
        {"id": 2, "partner_id": [2, "Azure Interior"], "amount_total": 250.0,
         "invoice_date": "2026-06-26", "move_type": "out_invoice"},
    ]}, confirm_capture=cap)
    out = fn("post_invoice")("Azure")
    assert "nhiều" in out.lower()
    assert "100" in out and "250" in out          # both amounts listed
    assert cap == []                               # nothing posted


def test_post_invoice_amount_disambiguator_filters_and_posts(monkeypatch):
    cap = []
    calls = patch_odoo(monkeypatch, {
        ("account.move", "search_read"): [
            {"id": 1, "partner_id": [2, "Azure Interior"], "amount_total": 100.0,
             "invoice_date": "2026-06-27", "move_type": "out_invoice"}],
        ("account.move", "read"): [{"name": "INV/2026/00011"}],
    }, confirm_capture=cap)
    out = fn("post_invoice")("Azure", amount=100.0)
    assert ["amount_total", "=", 100.0] in calls[0]["args"][0]
    assert "đã phát hành" in out.lower() and "INV/2026/00011" in out
    assert ("account.move", "action_post", [[1]]) in cap


def test_post_invoice_single_draft_posts(monkeypatch):
    cap = []
    patch_odoo(monkeypatch, {
        ("account.move", "search_read"): [
            {"id": 58, "partner_id": [15, "Azure Interior"], "amount_total": 100.0,
             "invoice_date": "2026-06-27", "move_type": "out_invoice"}],
        ("account.move", "read"): [{"name": "INV/2026/00012"}],
    }, confirm_capture=cap)
    out = fn("post_invoice")("Azure")
    assert "đã phát hành" in out.lower() and "INV/2026/00012" in out
    assert ("account.move", "action_post", [[58]]) in cap


# ── validate_picking ──────────────────────────────────────────────────────────

def test_validate_picking_not_found(monkeypatch):
    patch_odoo(monkeypatch, {"stock.picking": []})
    assert "không tìm thấy" in fn("validate_picking")("WH/OUT/99999").lower()


def test_validate_picking_ambiguous(monkeypatch):
    patch_odoo(monkeypatch, {"stock.picking": [
        {"id": 1, "name": "WH/OUT/0001", "state": "assigned"},
        {"id": 2, "name": "WH/OUT/0001", "state": "assigned"},
    ]})
    assert "nhiều" in fn("validate_picking")("WH/OUT/0001").lower()


def test_validate_picking_done_idempotent(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"stock.picking": [{"id": 10, "name": "WH/OUT/0010",
                                    "state": "done"}]},
               confirm_capture=cap)
    out = fn("validate_picking")("WH/OUT/0010")
    assert "đã được xác nhận" in out.lower()
    assert cap == []


def test_validate_picking_cancelled(monkeypatch):
    cap = []
    patch_odoo(monkeypatch,
               {"stock.picking": [{"id": 11, "name": "WH/OUT/0011",
                                    "state": "cancel"}]},
               confirm_capture=cap)
    out = fn("validate_picking")("WH/OUT/0011")
    assert "hủy" in out.lower()
    assert cap == []


def test_validate_picking_not_assigned_refused(monkeypatch):
    for bad_state in ("draft", "waiting", "confirmed"):
        cap = []
        patch_odoo(monkeypatch,
                   {"stock.picking": [{"id": 20, "name": "WH/OUT/0020",
                                        "state": bad_state}]},
                   confirm_capture=cap)
        out = fn("validate_picking")("WH/OUT/0020")
        assert "chưa sẵn sàng" in out.lower() or "chưa" in out.lower()
        assert cap == []


def test_validate_picking_assigned_calls_button_validate(monkeypatch):
    """assigned → button_validate directly (Odoo 19 auto-sets done qty on reserve)."""
    call_order = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        if method == "search_read":
            return [{"id": 30, "name": "WH/OUT/0030", "state": "assigned"}]
        call_order.append(method)
        return True

    monkeypatch.setattr(server, "odoo", fake_odoo)
    out = fn("validate_picking")("WH/OUT/0030")
    assert call_order == ["button_validate"]
    assert "đã xác nhận" in out.lower()


def test_validate_picking_wizard_fallback(monkeypatch):
    """If button_validate returns a dict (unexpected wizard), emit safe message."""
    call_order = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        if method == "search_read":
            return [{"id": 31, "name": "WH/OUT/0031", "state": "assigned"}]
        call_order.append(method)
        if method == "button_validate":
            return {"type": "ir.actions.act_window", "res_model": "stock.backorder.confirmation"}
        return True

    monkeypatch.setattr(server, "odoo", fake_odoo)
    out = fn("validate_picking")("WH/OUT/0031")
    assert "cần thao tác bổ sung" in out.lower() or "bổ sung" in out.lower()
    assert call_order == ["button_validate"]


# ── resolve_unique helper ─────────────────────────────────────────────────────

def test_resolve_unique_empty_returns_not_found():
    row, msg = server.resolve_unique([], "hóa đơn nháp", lambda r: r["x"])
    assert row is None
    assert "không tìm thấy" in msg.lower()


def test_resolve_unique_single_returns_row():
    only = {"id": 1, "x": "A"}
    row, msg = server.resolve_unique([only], "hóa đơn nháp", lambda r: r["x"])
    assert row is only
    assert msg is None


def test_resolve_unique_many_lists_candidates_with_hint():
    rows = [{"x": "Azure — 100đ"}, {"x": "Azure — 250đ"}]
    row, msg = server.resolve_unique(
        rows, "hóa đơn nháp", lambda r: r["x"], hint="Nêu rõ số tiền.")
    assert row is None
    assert "nhiều" in msg.lower()
    assert "Azure — 100đ" in msg and "Azure — 250đ" in msg
    assert "Nêu rõ số tiền." in msg


# ── create_quotation ──────────────────────────────────────────────────────────

def test_create_quotation_empty_lines_asks(monkeypatch):
    cap = []
    patch_odoo(monkeypatch, {}, confirm_capture=cap)
    out = fn("create_quotation")("Azure", [])
    assert "sản phẩm" in out.lower()
    assert cap == []


def test_create_quotation_partner_not_found(monkeypatch):
    cap = []
    patch_odoo(monkeypatch, {("res.partner", "search_read"): []},
               confirm_capture=cap)
    out = fn("create_quotation")("Nobody", [{"product": "bàn", "qty": 2}])
    assert "không tìm thấy" in out.lower()
    assert cap == []  # no create


def test_create_quotation_product_ambiguous_aborts(monkeypatch):
    cap = []
    patch_odoo(monkeypatch, {
        ("res.partner", "search_read"): [
            {"id": 7, "name": "Azure Interior", "email": "a@x.com"}],
        ("product.product", "search_read"): [
            {"id": 1, "name": "Ghế họp A", "default_code": "C1", "list_price": 100.0},
            {"id": 2, "name": "Ghế họp B", "default_code": "C2", "list_price": 120.0}],
    }, confirm_capture=cap)
    out = fn("create_quotation")("Azure", [{"product": "ghế họp", "qty": 5}])
    assert "nhiều" in out.lower() and "ghế họp" in out.lower()
    assert not any(c[1] == "create" for c in cap)  # nothing created


def test_create_quotation_happy_builds_order_lines(monkeypatch):
    cap = []
    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        if method == "create":
            cap.append((model, method, args))
            return 99
        if model == "res.partner":
            return [{"id": 7, "name": "Azure Interior", "email": "a@x.com"}]
        if model == "product.product":
            # domain = ["|", ["name","ilike",term], ["default_code","ilike",term], ["sale_ok","=",True]]
            # the searched term is the value of the first leaf: args[0][1][2]
            term = args[0][1][2]
            pid = 11 if "bàn" in term else 12
            return [{"id": pid, "name": f"SP {pid}", "default_code": "X",
                     "list_price": 50.0}]
        if model == "sale.order" and method == "read":
            return [{"name": "S00021"}]
        return []
    monkeypatch.setattr(server, "odoo", fake_odoo)

    out = fn("create_quotation")("Azure", [{"product": "bàn gỗ", "qty": 10},
                                           {"product": "ghế", "qty": 5}])
    assert "S00021" in out and "Azure Interior" in out and "2 dòng" in out
    create_calls = [c for c in cap if c[1] == "create"]
    assert len(create_calls) == 1
    vals = create_calls[0][2][0]              # args[0] = the vals dict
    assert vals["partner_id"] == 7
    assert vals["order_line"] == [
        (0, 0, {"product_id": 11, "product_uom_qty": 10}),
        (0, 0, {"product_id": 12, "product_uom_qty": 5}),
    ]


# ── create_rfq ────────────────────────────────────────────────────────────────

def test_create_rfq_empty_lines_asks(monkeypatch):
    cap = []
    patch_odoo(monkeypatch, {}, confirm_capture=cap)
    out = fn("create_rfq")("Gemini Furniture", [])
    assert "sản phẩm" in out.lower()
    assert cap == []


def test_create_rfq_supplier_not_found(monkeypatch):
    cap = []
    patch_odoo(monkeypatch, {("res.partner", "search_read"): []},
               confirm_capture=cap)
    out = fn("create_rfq")("Nobody", [{"product": "Screw", "qty": 2}])
    assert "không tìm thấy" in out.lower()
    assert cap == []  # no create


def test_create_rfq_product_ambiguous_aborts(monkeypatch):
    cap = []
    patch_odoo(monkeypatch, {
        ("res.partner", "search_read"): [
            {"id": 10, "name": "Gemini Furniture", "email": "g@x.com"}],
        ("product.product", "search_read"): [
            {"id": 1, "name": "Bolt A", "default_code": "B1", "list_price": 1.0},
            {"id": 2, "name": "Bolt B", "default_code": "B2", "list_price": 1.2}],
    }, confirm_capture=cap)
    out = fn("create_rfq")("Gemini", [{"product": "bolt", "qty": 5}])
    assert "nhiều" in out.lower() and "bolt" in out.lower()
    assert not any(c[1] == "create" for c in cap)  # nothing created


def test_create_rfq_happy_builds_order_lines(monkeypatch):
    cap = []
    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        if method == "create":
            cap.append((model, method, args))
            return 88
        if model == "res.partner":
            return [{"id": 10, "name": "Gemini Furniture", "email": "g@x.com"}]
        if model == "product.product":
            # domain leaf: args[0][1][2] is the searched term
            term = args[0][1][2]
            pid = 60 if "screw" in term.lower() else 59
            return [{"id": pid, "name": f"P {pid}", "default_code": "X",
                     "list_price": 0.5}]
        if model == "purchase.order" and method == "read":
            return [{"name": "P00013"}]
        return []
    monkeypatch.setattr(server, "odoo", fake_odoo)

    out = fn("create_rfq")("Gemini", [{"product": "Screw", "qty": 7},
                                      {"product": "Bolt", "qty": 5}])
    assert "P00013" in out and "Gemini Furniture" in out and "2 dòng" in out
    create_calls = [c for c in cap if c[1] == "create"]
    assert len(create_calls) == 1
    assert create_calls[0][0] == "purchase.order"
    vals = create_calls[0][2][0]              # args[0] = the vals dict
    assert vals["partner_id"] == 10
    assert vals["order_line"] == [
        (0, 0, {"product_id": 60, "product_qty": 7}),
        (0, 0, {"product_id": 59, "product_qty": 5}),
    ]
