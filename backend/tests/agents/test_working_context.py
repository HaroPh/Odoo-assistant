import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.working_context import (derive_working_context,
                                                enforce_explicit_ref)


def _env(ok=True, ref="S00040", model="sale.order", display="Đã tạo báo giá S00040 (nháp)."):
    return {"ok": ok, "ref": ref, "model": model, "res_id": 42,
            "state": "draft", "display": display}


# ── derive_working_context ────────────────────────────────────────────────────

def test_derive_sale_order_envelope():
    wc = derive_working_context(_env())
    assert wc == {"ref": "S00040", "model": "sale.order",
                  "display": "Đã tạo báo giá S00040 (nháp)."}


def test_derive_purchase_order_envelope():
    wc = derive_working_context(_env(ref="P00015", model="purchase.order"))
    assert wc["ref"] == "P00015" and wc["model"] == "purchase.order"


def test_derive_rejects_invoice_and_failure():
    assert derive_working_context(_env(model="account.move", ref=None)) is None
    assert derive_working_context(_env(model="account.move")) is None
    assert derive_working_context(_env(ok=False)) is None
    assert derive_working_context(_env(ref=None)) is None
    assert derive_working_context(_env(ref="")) is None


def test_derive_is_total_on_garbage():
    # Invariant B: never raises, any input → dict | None.
    for garbage in (None, "x", 42, [], {}, {"ok": True},
                    {"ok": True, "ref": "S1", "model": None},
                    {"ok": object(), "ref": object(), "model": object()}):
        out = derive_working_context(garbage)
        assert out is None or isinstance(out, dict)


def test_derive_display_never_none():
    wc = derive_working_context(_env(display=None))
    assert wc["display"] == ""


def test_derive_returns_only_three_keys():
    assert set(derive_working_context(_env())) == {"ref", "model", "display"}


# ── enforce_explicit_ref ──────────────────────────────────────────────────────

def _plan(order_ref="S00040", tool="confirm_sale_order"):
    return {"tool": tool, "args": {"order_ref": order_ref}, "summary": "x"}


def test_enforce_overrides_with_single_explicit_ref():
    out = enforce_explicit_ref(_plan("S00040"), "xác nhận đơn S00007 giúp tôi")
    assert out["args"]["order_ref"] == "S00007"


def test_enforce_does_not_mutate_original_plan():
    plan = _plan("S00040")
    enforce_explicit_ref(plan, "xác nhận đơn S00007")
    assert plan["args"]["order_ref"] == "S00040"


def test_enforce_keeps_plan_when_ref_matches():
    plan = _plan("S00007")
    assert enforce_explicit_ref(plan, "xác nhận S00007") is plan


def test_enforce_noop_without_explicit_ref():
    plan = _plan()
    assert enforce_explicit_ref(plan, "xác nhận đơn vừa tạo") is plan


def test_enforce_noop_with_two_different_refs():
    plan = _plan()
    assert enforce_explicit_ref(plan, "so sánh S00007 và S00008") is plan


def test_enforce_same_ref_twice_counts_once():
    out = enforce_explicit_ref(_plan("S00040"), "đơn S00007, đúng, S00007 ấy")
    assert out["args"]["order_ref"] == "S00007"


def test_enforce_noop_when_plan_lacks_order_ref():
    plan = {"tool": "create_quotation",
            "args": {"partner_name": "Azure", "lines": []}, "summary": "x"}
    assert enforce_explicit_ref(plan, "tạo báo giá S00007 kiểu gì đó") is plan


def test_enforce_case_sensitive_ignores_lowercase():
    plan = _plan("S00040")
    assert enforce_explicit_ref(plan, "xác nhận s00007") is plan


def test_enforce_is_total_on_garbage():
    for plan in (None, "x", 42, {}, {"args": None}, {"args": "x"},
                 {"args": {"order_ref": None}}):
        out = enforce_explicit_ref(plan, "S00007")   # must not raise
        assert out == plan or isinstance(out, dict)
    assert enforce_explicit_ref(_plan(), None) == _plan()


def test_enforce_is_total_on_bad_user_text():
    # Invariant B for the second arg: non-string/non-None user_text must not raise.
    plan = _plan("S00040")
    for bad_text in (42, ["S00007"], object(), 3.14, {"ref": "S00007"}):
        out = enforce_explicit_ref(plan, bad_text)   # must not raise
        assert out is plan or isinstance(out, dict)


def test_enforce_non_dict_plan_returns_same_object():
    # Pins identity (not just equality) for the one no-op path the garbage
    # loop doesn't distinguish is-vs-== for.
    for plan in (None, "x", 42):
        assert enforce_explicit_ref(plan, "S00007") is plan
