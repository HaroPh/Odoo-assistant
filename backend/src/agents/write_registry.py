# backend/src/agents/write_registry.py
"""Single source of truth for coordinated write flows. Adding a coordinated write =
one row here; the planner branch, router, and graph registration all read it."""

from dataclasses import dataclass
from typing import Callable

from .create_order import make_order_node, SALE_CFG, PURCHASE_CFG
from .edit_order import make_edit_order_node, SALE_EDIT_CFG, PURCHASE_EDIT_CFG
from .inventory_write import make_inventory_node


@dataclass(frozen=True)
class Spec:
    node: str                 # graph node name
    build: Callable           # (llm, tools) -> node


WRITE_COORDINATORS = {
    "create_quotation":     Spec("create_order",    lambda llm, tools: make_order_node(tools, SALE_CFG)),
    "create_rfq":           Spec("create_rfq",      lambda llm, tools: make_order_node(tools, PURCHASE_CFG)),
    "update_quotation_lines": Spec("edit_order", lambda llm, tools: make_edit_order_node(tools, SALE_EDIT_CFG)),
    "update_rfq_lines":       Spec("edit_rfq",   lambda llm, tools: make_edit_order_node(tools, PURCHASE_EDIT_CFG)),
    "inventory_adjustment": Spec("inventory_adjust", lambda llm, tools: make_inventory_node(tools)),
}

COORDINATED_TOOLS = frozenset(WRITE_COORDINATORS)


@dataclass(frozen=True)
class NextStep:
    label: str                       # menu label, e.g. "Xác nhận báo giá"
    tool: str                        # next tool in the chain
    args: Callable[[dict], dict]     # last_write -> args for that tool


# Linear next step per chain tool; absence = terminal. Adding a purchase chain
# later = envelope-ize its tools + add rows here (no node changes).
NEXT_STEPS = {
    # ── chuỗi bán ──
    "create_quotation":          NextStep("Xác nhận báo giá", "confirm_sale_order",
                                          lambda lw: {"order_ref": lw["ref"]}),
    # sửa đơn nháp → gợi ý xác nhận (giống sau khi tạo mới)
    "update_quotation_lines":    NextStep("Xác nhận báo giá", "confirm_sale_order",
                                          lambda lw: {"order_ref": lw["ref"]}),
    "confirm_sale_order":        NextStep("Giao hàng", "deliver_order",
                                          lambda lw: {"order_ref": lw["ref"]}),
    "deliver_order":             NextStep("Tạo hóa đơn", "create_invoice_from_order",
                                          lambda lw: {"order_ref": lw["ref"]}),
    "create_invoice_from_order": NextStep("Phát hành hóa đơn", "post_invoice",
                                          lambda lw: {"invoice_id": lw["res_id"]}),
    # ── chuỗi mua ──
    "create_rfq":                NextStep("Xác nhận đơn mua", "confirm_purchase_order",
                                          lambda lw: {"order_ref": lw["ref"]}),
    "update_rfq_lines":          NextStep("Xác nhận đơn mua", "confirm_purchase_order",
                                          lambda lw: {"order_ref": lw["ref"]}),
    "confirm_purchase_order":    NextStep("Nhận hàng", "receive_order",
                                          lambda lw: {"order_ref": lw["ref"]}),
    "receive_order":             NextStep("Tạo hóa đơn NCC", "create_bill_from_po",
                                          lambda lw: {"order_ref": lw["ref"]}),
    "create_bill_from_po":       NextStep("Phát hành hóa đơn", "post_invoice",
                                          lambda lw: {"invoice_id": lw["res_id"]}),
}
