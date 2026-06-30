"""Read-only policy over a Transport. The ONLY way erp_query reaches Odoo.
Four hard guards: model-name sanitize, method allowlist, model denylist, forced
limit. Read-only by construction — there is no write method here."""
import os
import re

from .transport import Transport, XmlRpcTransport

READ_METHODS = {"search_read", "read_group", "name_search"}
MODEL_DENYLIST = {
    "res.users", "ir.config_parameter", "ir.model.access",
    "account.journal", "account.bank.statement",
}
MAX_LIMIT = 100
_MODEL_RE = re.compile(r"^[a-zA-Z0-9._]+$")


class GatewayError(Exception):
    pass


class Gateway:
    def __init__(self, transport: Transport) -> None:
        self._t = transport

    def _check_model(self, model: str) -> None:
        if not model or not _MODEL_RE.match(model):
            raise GatewayError(f"Tên model không hợp lệ: {model!r}")
        if model in MODEL_DENYLIST:
            raise GatewayError(f"Model bị cấm truy cập: {model}")

    def _call(self, model: str, method: str, args: list, kwargs: dict):
        if method not in READ_METHODS:
            raise GatewayError(f"Method '{method}' không được phép (chỉ đọc).")
        self._check_model(model)
        return self._t.call(model, method, args, kwargs)

    def search_read(self, model, domain, fields, order=None, limit=50, context=None):
        kw = {"fields": fields, "limit": min(int(limit or MAX_LIMIT), MAX_LIMIT)}
        if order:
            kw["order"] = order
        if context:
            kw["context"] = context
        return self._call(model, "search_read", [domain], kw)

    def read_group(self, model, domain, fields, groupby, orderby=None, limit=None, lazy=False):
        kw = {"lazy": lazy}
        if orderby:
            kw["orderby"] = orderby
        if limit:
            kw["limit"] = min(int(limit), MAX_LIMIT)
        return self._call(model, "read_group", [domain, fields, groupby], kw)

    def name_search(self, model, name, limit=5):
        return self._call(model, "name_search", [name],
                          {"limit": min(int(limit or 5), MAX_LIMIT)})


_default: Gateway | None = None


def default_gateway() -> Gateway:
    """Lazy singleton built from the backend's ODOO_* env (already provided by
    start-dev.ps1)."""
    global _default
    if _default is None:
        _default = Gateway(XmlRpcTransport(
            os.environ["ODOO_URL"], os.environ["ODOO_DB"],
            os.environ["ODOO_USERNAME"], os.environ["ODOO_PASSWORD"]))
    return _default
