import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.create_order import resolve_entity_for_order, render_draft


def _env(matches, needs, status="success"):
    return {"status": status, "data": {"matches": matches, "needs_disambiguation": needs},
            "display": "x"}


def test_resolve_single_match_ok():
    kind, val = resolve_entity_for_order(
        _env([{"id": 41, "name": "Azur Interior", "score": 1.0}], False), "Azur Interior")
    assert kind == "ok" and val == {"id": 41, "name": "Azur Interior"}


def test_resolve_zero_match_none():
    kind, val = resolve_entity_for_order(_env([], False), "Nobody")
    assert kind == "none" and val is None


def test_resolve_ambiguous_returns_options():
    kind, val = resolve_entity_for_order(
        _env([{"id": 41, "name": "Azur Interior", "score": .6},
              {"id": 52, "name": "Azur Furniture", "score": .6}], True), "Azur")
    assert kind == "ambiguous"
    assert val == [{"id": 41, "name": "Azur Interior"}, {"id": 52, "name": "Azur Furniture"}]


def test_resolve_multiple_one_exact_picks_exact():
    kind, val = resolve_entity_for_order(
        _env([{"id": 41, "name": "Azur Interior", "score": .6},
              {"id": 52, "name": "Azur Interior Plus", "score": .6}], False), "Azur Interior")
    assert kind == "ok" and val["id"] == 41


def test_resolve_error_envelope():
    kind, val = resolve_entity_for_order(
        {"status": "error", "data": None, "display": "Lỗi tra cứu", "error": "boom"}, "x")
    assert kind == "error" and "Lỗi" in val


def test_render_draft_has_lines_and_total():
    out = render_draft({"id": 41, "name": "Azur"},
                       [{"product_id": 552, "name": "Tủ", "qty": 3,
                         "unit_price": 100000.0, "subtotal": 300000.0}], 300000.0)
    assert "Azur" in out and "Tủ" in out
    assert "300,000" in out
    assert "có / không" in out
