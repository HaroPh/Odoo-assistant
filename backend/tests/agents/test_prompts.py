import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))

from backend.src.agents.prompts import WRITE_PLANNER_PROMPT


def test_planner_prompt_advertises_confirm_sale_order_contract():
    # The planner must emit the exact tool name + arg key the executor accepts.
    assert "confirm_sale_order" in WRITE_PLANNER_PROMPT
    assert "order_ref" in WRITE_PLANNER_PROMPT
