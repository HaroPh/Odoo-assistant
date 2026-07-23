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
    "action_validate": "write",
    "action_apply_inventory": "write",
    "action_register_payment": "write",
    "action_create_payments": "write",
    "convert_opportunity": "write",
    "button_mark_done": "write",
    "action_assign": "write",
    "action_create_returns": "write",
    "refund_moves": "write",
    # UNLINK — Phase 3: cần confirmation + cảnh báo
    "unlink": "unlink", "action_delete": "unlink",
}

def classify_operation(method: str) -> str | None:
    """None = method không được phép (deny-by-default)."""
    return ODOO_METHOD_OPERATION_MAP.get(str(method).lower().strip())

def sanitize_model(name: str) -> str:
    """Chặn injection qua tên model — chỉ cho [a-zA-Z0-9._]."""
    if not name or not re.match(r"^[a-zA-Z0-9._]+$", name):
        raise ValueError(f"Tên model không hợp lệ: {name!r}")
    return name.strip()


_FIELD_KEY_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

def sanitize_payload_keys(value, *, _depth: int = 0, _max_depth: int = 10) -> None:
    """Đệ quy quét mọi dict trong args/kwargs của odoo(), validate KEY-SHAPE
    (Odoo field name luôn snake_case identifier). VALUE không bị đụng — có
    thể là bất kỳ cấu trúc nào (list, command-tuple, nested dict) tùy
    model/field. Raise ValueError ở key đầu tiên sai hình dạng. Side-effect-
    free — chỉ kiểm tra, không sửa value tại chỗ. An toàn gọi trên cả args
    (list) lẫn kwargs (dict)."""
    if _depth > _max_depth:
        raise ValueError("Payload lồng quá sâu — nghi ngờ dữ liệu bất thường")
    if isinstance(value, dict):
        for k, v in value.items():
            if not (isinstance(k, str) and _FIELD_KEY_RE.fullmatch(k)):
                raise ValueError(f"Tên field không hợp lệ trong payload: {k!r}")
            sanitize_payload_keys(v, _depth=_depth + 1, _max_depth=_max_depth)
    elif isinstance(value, (list, tuple)):
        for item in value:
            sanitize_payload_keys(item, _depth=_depth + 1, _max_depth=_max_depth)


def forbid_extra_kwargs(tool_manager) -> None:
    """Ép mọi tool đã đăng ký trong tool_manager reject kwarg lạ trong
    tool-call thay vì âm thầm bỏ qua (Pydantic mặc định extra='ignore'),
    đồng thời refresh JSON schema quảng cáo cho LLM (tool.parameters) để
    phản ánh đúng giới hạn additionalProperties: false. tool_manager nhận
    qua tham số (không import trực tiếp mcp) để tránh circular import với
    server.py."""
    for tool in tool_manager.list_tools():
        model = tool.fn_metadata.arg_model
        model.model_config["extra"] = "forbid"
        model.model_rebuild(force=True)
        tool.parameters = model.model_json_schema(by_alias=True)
