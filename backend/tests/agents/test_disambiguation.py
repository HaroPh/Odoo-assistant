import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.agents.disambiguation import parse_selection

OPTS = [{"id": 41, "name": "Azur Interior"}, {"id": 52, "name": "Azur Furniture"}]


def test_integer_index_one_based():
    assert parse_selection("1", OPTS) == 41
    assert parse_selection(" 2 ", OPTS) == 52


def test_integer_out_of_range_is_none():
    assert parse_selection("3", OPTS) is None
    assert parse_selection("0", OPTS) is None


def test_exact_name_match_case_insensitive():
    assert parse_selection("azur furniture", OPTS) == 52


def test_unique_substring_match():
    assert parse_selection("furniture", OPTS) == 52


def test_ambiguous_substring_is_none():
    assert parse_selection("azur", OPTS) is None


def test_garbage_is_none():
    assert parse_selection("", OPTS) is None
    assert parse_selection("xyz", OPTS) is None
