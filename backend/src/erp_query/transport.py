"""Wire to Odoo. Transport is swappable (XML-RPC today, JSON-2 later); it carries
NO policy — the gateway does. Keep this file free of any allowlist/limit logic so
the JSON-2 swap stays a one-file change."""
from typing import Any, Protocol
import xmlrpc.client


class Transport(Protocol):
    def call(self, model: str, method: str, args: list, kwargs: dict) -> Any: ...


class XmlRpcTransport:
    """Odoo XML-RPC via execute_kw (Odoo 19; deprecated, removed in Odoo 20)."""

    def __init__(self, url: str, db: str, user: str, password: str) -> None:
        self._url, self._db, self._user, self._pwd = url, db, user, password
        self._uid: int | None = None

    def _uid_(self) -> int:
        if self._uid is None:
            common = xmlrpc.client.ServerProxy(self._url + "/xmlrpc/2/common")
            self._uid = common.authenticate(self._db, self._user, self._pwd, {})
            if not self._uid:
                raise RuntimeError("Odoo authentication failed — kiểm tra ODOO_USERNAME/PASSWORD")
        return self._uid

    def call(self, model: str, method: str, args: list, kwargs: dict) -> Any:
        uid = self._uid_()
        obj = xmlrpc.client.ServerProxy(self._url + "/xmlrpc/2/object")
        return obj.execute_kw(self._db, uid, self._pwd, model, method, args, kwargs or {})


class Json2Transport:
    """Placeholder for the Odoo 19 JSON-2 (/json/2/) + API-key path. Implement when
    migrating off XML-RPC; the gateway above is unaffected."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url, self._api_key = base_url, api_key

    def call(self, model: str, method: str, args: list, kwargs: dict) -> Any:
        raise NotImplementedError("JSON-2 transport chưa triển khai — dùng XmlRpcTransport.")
