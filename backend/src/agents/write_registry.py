# backend/src/agents/write_registry.py
"""Single source of truth for coordinated write flows. Adding a coordinated write =
one row here; the planner branch, router, and graph registration all read it."""

from dataclasses import dataclass
from typing import Callable

from .create_order import make_order_node, SALE_CFG, PURCHASE_CFG
from .inventory_write import make_inventory_node


@dataclass(frozen=True)
class Spec:
    node: str                 # graph node name
    build: Callable           # (llm, tools) -> node


WRITE_COORDINATORS = {
    "create_quotation":     Spec("create_order",    lambda llm, tools: make_order_node(tools, SALE_CFG)),
    "create_rfq":           Spec("create_rfq",      lambda llm, tools: make_order_node(tools, PURCHASE_CFG)),
    "inventory_adjustment": Spec("inventory_adjust", lambda llm, tools: make_inventory_node(tools)),
}

COORDINATED_TOOLS = frozenset(WRITE_COORDINATORS)
