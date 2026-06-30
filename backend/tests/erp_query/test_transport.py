import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from unittest.mock import MagicMock, patch
from backend.src.erp_query.transport import XmlRpcTransport, Json2Transport


def test_xmlrpc_transport_calls_execute_kw_with_auth():
    with patch("backend.src.erp_query.transport.xmlrpc.client.ServerProxy") as SP:
        common = MagicMock(); common.authenticate.return_value = 7
        obj = MagicMock(); obj.execute_kw.return_value = [{"id": 1}]
        SP.side_effect = [common, obj]
        t = XmlRpcTransport("http://odoo", "db", "u", "p")
        out = t.call("res.partner", "search_read", [[]], {"fields": ["name"]})
        assert out == [{"id": 1}]
        obj.execute_kw.assert_called_once_with(
            "db", 7, "p", "res.partner", "search_read", [[]], {"fields": ["name"]})


def test_json2_transport_is_stub():
    with pytest.raises(NotImplementedError):
        Json2Transport("http://odoo/json/2", "key").call("res.partner", "search_read", [[]], {})
