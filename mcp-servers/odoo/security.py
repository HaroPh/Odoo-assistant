"""Deny-by-default method allowlist + input sanitization (spec 2026-07-13-
mcp-server-modularization). Port từ mcp_server addon (controllers/utils.py)."""
import re

# Ánh xạ XML-RPC method → loại operation. Method không có trong map = bị từ chối
# (deny-by-default). Phase 3 dùng "create|write|unlink" để biết cần confirmation gate.
ODOO_METHOD_OPERATION_MAP = {
    # READ — an toàn
    "read": "read", "search": "read", "search_read": "read",
    "search_count": "read", "name_search": "read", "fields_get": "read",
    "read_group": "read", "formatted_read_group": "read",
    "default_get": "read", "name_get": "read", "get_metadata": "read",
    # CREATE — Phase 3: cần confirmation
    "create": "create", "copy": "create", "name_create": "create",
    "create_invoices": "create",
    "action_create_invoice": "create",
    # WRITE — Phase 3: cần confirmation
    "write": "write", "toggle_active": "write",
    "action_archive": "write", "message_post": "write",
    "action_confirm": "write",
    "button_confirm": "write",
    "action_post": "write",
    "button_validate": "write",
    "action_apply_inventory": "write",
    # UNLINK — Phase 3: cần confirmation + cảnh báo
    "unlink": "unlink", "action_delete": "unlink",
}

def classify_operation(method: str) -> str | None:
    """None = method không được phép (deny-by-default)."""
    return ODOO_METHOD_OPERATION_MAP.get(str(method).lower().strip())

def sanitize_model(name: str) -> str:
    """Chặn injection qua tên model — chỉ cho [a-zA-Z0-9._]."""
    name = name.strip() if name else name
    if not name or not re.match(r"^[a-zA-Z0-9._]+$", name):
        raise ValueError(f"Tên model không hợp lệ: {name!r}")
    return name
