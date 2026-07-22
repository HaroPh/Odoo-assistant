import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


class Seq:
    def __init__(self, *items):
        self.items = list(items)

    def next(self):
        return self.items.pop(0)


def _fake(monkeypatch, responses):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        v = responses.get((model, method), [])
        if isinstance(v, Exception):
            raise v
        return v.next() if isinstance(v, Seq) else v

    monkeypatch.setattr(server, "odoo", fake_odoo)
    return calls


# Office Lamp: variant 39, template 24 (id-space collision defense: must differ)
_LAMP = [{"id": 39, "name": "[FURN_8888] Office Lamp",
          "product_tmpl_id": [24, "[FURN_8888] Office Lamp"]}]
COMP_A, COMP_B = 67, 68


def _bom(id=9, code="AI-BOM", type="normal", active=True, tmpl=24, batch=1.0):
    return {"id": id, "code": code, "type": type, "active": active,
            "product_tmpl_id": [tmpl, "x"], "product_qty": batch}


def _line(id, pid, qty, name="comp"):
    return {"id": id, "product_id": [pid, name], "product_qty": qty}


# ── create_bom ───────────────────────────────────────────────────────────────

def test_create_bom_empty_components(monkeypatch):
    calls = _fake(monkeypatch, {})
    out = _env(fn("create_bom")(39, []))
    assert out["ok"] is False and "nguyên liệu" in out["display"]
    assert calls == []


def test_create_bom_nonpositive_batch(monkeypatch):
    calls = _fake(monkeypatch, {})
    out = _env(fn("create_bom")(39, [{"product_id": COMP_A, "qty": 2}], 0))
    assert out["ok"] is False and "mỗi mẻ" in out["display"]
    assert calls == []


def test_create_bom_component_bad_qty(monkeypatch):
    _fake(monkeypatch, {})
    out = _env(fn("create_bom")(39, [{"product_id": COMP_A, "qty": 0}]))
    assert out["ok"] is False and "số lượng" in out["display"].lower()


def test_create_bom_component_is_self(monkeypatch):
    _fake(monkeypatch, {("product.product", "search_read"): _LAMP})
    out = _env(fn("create_bom")(39, [{"product_id": 39, "qty": 1}]))
    assert out["ok"] is False and "chính thành phẩm" in out["display"]


def test_create_bom_product_not_found(monkeypatch):
    _fake(monkeypatch, {("product.product", "search_read"): []})
    out = _env(fn("create_bom")(999, [{"product_id": COMP_A, "qty": 1}]))
    assert out["ok"] is False and "Không tìm thấy sản phẩm" in out["display"]


def test_create_bom_happy_uses_template_and_tuple_lines(monkeypatch):
    calls = _fake(monkeypatch, {
        ("product.product", "search_read"): _LAMP,
        ("mrp.bom", "create"): 9,
        ("mrp.bom", "search_read"): [_bom(code="AI-BOM")],
    })
    out = _env(fn("create_bom")(39, [{"product_id": COMP_A, "qty": 2},
                                    {"product_id": COMP_B, "qty": 1}],
                                batch_qty=1.0, code="AI-BOM"))
    assert out["ok"] is True
    assert out["ref"] == "AI-BOM" and out["model"] == "mrp.bom"
    assert out["res_id"] == 9 and out["state"] == "active"
    create = next(c for c in calls if c["method"] == "create")
    vals = create["args"][0]
    # bẫy id-space: product_tmpl_id lấy từ TEMPLATE (24), không phải variant (39)
    assert vals["product_tmpl_id"] == 24
    assert vals["product_qty"] == 1.0
    assert vals["code"] == "AI-BOM"
    # tuple (0,0,{...}) shape đúng, component = variant id
    assert vals["bom_line_ids"] == [
        (0, 0, {"product_id": COMP_A, "product_qty": 2.0}),
        (0, 0, {"product_id": COMP_B, "product_qty": 1.0})]


def test_create_bom_omits_code_when_empty(monkeypatch):
    calls = _fake(monkeypatch, {
        ("product.product", "search_read"): _LAMP,
        ("mrp.bom", "create"): 9,
        ("mrp.bom", "search_read"): [_bom(code=False)],
    })
    out = _env(fn("create_bom")(39, [{"product_id": COMP_A, "qty": 2}]))
    assert out["ok"] is True
    create = next(c for c in calls if c["method"] == "create")
    assert "code" not in create["args"][0]     # KHÔNG gửi key code rỗng
    assert out["ref"] == "BoM #9"              # label fallback


def test_create_bom_is_kit_sets_phantom_type(monkeypatch):
    calls = _fake(monkeypatch, {
        ("product.product", "search_read"): _LAMP,
        ("mrp.bom", "create"): 9,
        ("mrp.bom", "search_read"): [_bom(code="AI-KIT", type="phantom")],
    })
    out = _env(fn("create_bom")(39, [{"product_id": COMP_A, "qty": 2}],
                                is_kit=True))
    assert out["ok"] is True
    create = next(c for c in calls if c["method"] == "create")
    assert create["args"][0]["type"] == "phantom"


def test_create_bom_normal_omits_type_key(monkeypatch):
    calls = _fake(monkeypatch, {
        ("product.product", "search_read"): _LAMP,
        ("mrp.bom", "create"): 9,
        ("mrp.bom", "search_read"): [_bom(code="AI-BOM")],
    })
    out = _env(fn("create_bom")(39, [{"product_id": COMP_A, "qty": 2}]))
    assert out["ok"] is True
    create = next(c for c in calls if c["method"] == "create")
    assert "type" not in create["args"][0]     # is_kit=False default -> unchanged behavior


# ── update_bom_lines ─────────────────────────────────────────────────────────

def test_update_bom_empty_changes(monkeypatch):
    calls = _fake(monkeypatch, {})
    out = _env(fn("update_bom_lines")(9, []))
    assert out["ok"] is False and "thay đổi" in out["display"]
    assert calls == []


def test_update_bom_not_found(monkeypatch):
    _fake(monkeypatch, {("mrp.bom", "search_read"): []})
    out = _env(fn("update_bom_lines")(999, [{"action": "add", "product_id": COMP_A, "qty": 1}]))
    assert out["ok"] is False and "Không tìm thấy BoM" in out["display"]


def test_update_bom_phantom_no_longer_rejected(monkeypatch):
    # Round 6 (Tier 4): update_bom_lines now supports Kit BoMs too — the
    # old hard rejection here was round 5's deliberate scope cut, not a
    # permanent restriction.
    _fake(monkeypatch, {
        ("mrp.bom", "search_read"): [_bom(type="phantom")],
        ("mrp.bom.line", "search_read"): [_line(18, COMP_A, 2.0, "Drawer Black")],
    })
    out = _env(fn("update_bom_lines")(9, [{"action": "set_qty", "product_id": COMP_A, "qty": 5}]))
    assert out["ok"] is True


def test_update_bom_add_existing_component(monkeypatch):
    _fake(monkeypatch, {
        ("mrp.bom", "search_read"): [_bom()],
        ("mrp.bom.line", "search_read"): [_line(18, COMP_A, 2.0, "Drawer Black")],
    })
    out = _env(fn("update_bom_lines")(9, [{"action": "add", "product_id": COMP_A, "qty": 3}]))
    assert out["ok"] is False and "đã có" in out["display"]


def test_update_bom_setqty_missing_component(monkeypatch):
    _fake(monkeypatch, {
        ("mrp.bom", "search_read"): [_bom()],
        ("mrp.bom.line", "search_read"): [_line(18, COMP_A, 2.0, "Drawer Black")],
    })
    out = _env(fn("update_bom_lines")(9, [{"action": "set_qty", "product_id": COMP_B, "qty": 3}]))
    assert out["ok"] is False and "chưa có" in out["display"]


def test_update_bom_remove_last_line_blocked(monkeypatch):
    _fake(monkeypatch, {
        ("mrp.bom", "search_read"): [_bom()],
        ("mrp.bom.line", "search_read"): [_line(18, COMP_A, 2.0, "Drawer Black")],
    })
    out = _env(fn("update_bom_lines")(9, [{"action": "remove", "product_id": COMP_A, "qty": None}]))
    assert out["ok"] is False and "ít nhất 1 nguyên liệu" in out["display"]


def test_update_bom_happy_builds_correct_ops(monkeypatch):
    calls = _fake(monkeypatch, {
        ("mrp.bom", "search_read"): [_bom()],
        ("mrp.bom.line", "search_read"): Seq(
            [_line(18, COMP_A, 2.0, "Drawer Black"),
             _line(19, COMP_B, 1.0, "Drawer Case Black")],
            [_line(18, COMP_A, 5.0, "Drawer Black")],   # re-read sau write
        ),
        ("mrp.bom", "write"): True,
    })
    out = _env(fn("update_bom_lines")(9, [
        {"action": "set_qty", "product_id": COMP_A, "qty": 5},
        {"action": "remove", "product_id": COMP_B, "qty": None},
        {"action": "add", "product_id": 31, "qty": 4}]))
    assert out["ok"] is True and out["ref"] == "AI-BOM"
    write = next(c for c in calls if c["method"] == "write")
    ops = write["args"][1]["bom_line_ids"]
    assert (1, 18, {"product_qty": 5.0}) in ops
    assert (2, 19, 0) in ops
    assert (0, 0, {"product_id": 31, "product_qty": 4.0}) in ops
    assert len(ops) == 3          # MỘT lệnh write, 3 op


def test_update_bom_duplicate_add_same_component_rejected(monkeypatch):
    # shown≠written bug (found in final whole-branch review): two `add` ops
    # for the SAME new component in one request must not both reach write() —
    # by_pid is a static snapshot from before the loop, so a naive check lets
    # both through, silently creating 2 lines for 1 component.
    calls = _fake(monkeypatch, {
        ("mrp.bom", "search_read"): [_bom()],
        ("mrp.bom.line", "search_read"): [_line(18, COMP_A, 2.0, "Drawer Black")],
    })
    out = _env(fn("update_bom_lines")(9, [
        {"action": "add", "product_id": 31, "qty": 2},
        {"action": "add", "product_id": 31, "qty": 3}]))
    assert out["ok"] is False and "đã có" in out["display"]
    assert not any(c["method"] == "write" for c in calls)


def test_update_bom_unknown_action(monkeypatch):
    _fake(monkeypatch, {
        ("mrp.bom", "search_read"): [_bom()],
        ("mrp.bom.line", "search_read"): [_line(18, COMP_A, 2.0)],
    })
    out = _env(fn("update_bom_lines")(9, [{"action": "frobnicate", "product_id": COMP_A, "qty": 1}]))
    assert out["ok"] is False


def test_update_bom_odoo_fault(monkeypatch):
    _fake(monkeypatch, {("mrp.bom", "search_read"): RuntimeError("Odoo down")})
    out = _env(fn("update_bom_lines")(9, [{"action": "add", "product_id": COMP_A, "qty": 1}]))
    assert out["ok"] is False and "Odoo down" in out["display"]
