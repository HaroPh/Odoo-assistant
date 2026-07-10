# backend/src/agents/write_gate.py
"""S3 (ADR-009 §2.2, F16): write-mode toggle đọc runtime từ Odoo
ir.config_parameter — bật/tắt từ Odoo UI (Settings → Technical → System
Parameters, key: erp_ai.write_actions_enabled, value: true), không cần
restart backend.

Đường đọc RIÊNG, hẹp, hardcode đúng 1 key — CỐ Ý không đi qua
erp_query.gateway: Gateway denylist ir.config_parameter để chặn agent tự ý
đọc system parameter qua đường query nhận input từ LLM; lookup này không có
input LLM nên tách riêng, KHÔNG nới lỏng denylist đó.

Fail-closed 1 rule duy nhất (spec §3): không đọc được / key chưa tồn tại /
value khác "true" → TẮT. Kill-switch phải khóa lại khi mơ hồ, không mở ra.
"""
import logging
import os
import time

logger = logging.getLogger(__name__)

KEY = "erp_ai.write_actions_enabled"
_CACHE_TTL_S = 5.0
_cache = {"value": False, "expires_at": 0.0}
_transport = None


def _get_transport():
    global _transport
    if _transport is None:
        from ..erp_query.transport import XmlRpcTransport
        _transport = XmlRpcTransport(
            os.environ["ODOO_URL"], os.environ["ODOO_DB"],
            os.environ["ODOO_USERNAME"], os.environ["ODOO_PASSWORD"])
    return _transport


def write_actions_enabled() -> bool:
    """True chỉ khi ir.config_parameter[KEY] == "true" (strip+lower) đọc được
    trong TTL. Cache cả kết quả lỗi — Odoo sập thì không spam retry mỗi lượt.
    Race giữa 2 lượt gọi đồng thời chỉ gây 1 lần đọc thừa — vô hại."""
    now = time.monotonic()
    if now < _cache["expires_at"]:
        return _cache["value"]
    try:
        rows = _get_transport().call(
            "ir.config_parameter", "search_read",
            [[("key", "=", KEY)]], {"fields": ["value"], "limit": 1})
        # Odoo XML-RPC trả False (không phải None) cho char field rỗng → `or ""`
        value = bool(rows) and str(rows[0].get("value") or "").strip().lower() == "true"
    except Exception as e:  # noqa: BLE001 — mọi lỗi đọc đều fail-closed (spec §3)
        logger.warning("write_gate: không đọc được toggle từ Odoo (%s) — fail-closed", e)
        value = False
    _cache["value"] = value
    _cache["expires_at"] = now + _CACHE_TTL_S
    return value
