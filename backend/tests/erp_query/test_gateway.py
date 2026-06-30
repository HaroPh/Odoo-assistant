import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from backend.src.erp_query.gateway import Gateway, GatewayError, MAX_LIMIT


class FakeTransport:
    def __init__(self, ret=None):
        self.ret = ret if ret is not None else []
        self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs))
        return self.ret


def test_search_read_passes_through_and_forces_limit():
    ft = FakeTransport([{"id": 1}])
    gw = Gateway(ft)
    out = gw.search_read("res.partner", [["customer_rank", ">", 0]], ["name"], limit=9999)
    assert out == [{"id": 1}]
    model, method, args, kwargs = ft.calls[0]
    assert method == "search_read"
    assert kwargs["limit"] == MAX_LIMIT          # capped
    assert args == [[["customer_rank", ">", 0]]]


def test_denylisted_model_rejected():
    gw = Gateway(FakeTransport())
    with pytest.raises(GatewayError):
        gw.search_read("res.users", [], ["login"])


def test_invalid_model_name_rejected():
    gw = Gateway(FakeTransport())
    with pytest.raises(GatewayError):
        gw.search_read("res partner; drop", [], ["name"])


def test_write_method_blocked_at_call_layer():
    gw = Gateway(FakeTransport())
    with pytest.raises(GatewayError):
        gw._call("res.partner", "create", [{}], {})


def test_name_search_shape():
    ft = FakeTransport([(1, "Azur Interior")])
    gw = Gateway(ft)
    out = gw.name_search("res.partner", "Azur", limit=5)
    assert out == [(1, "Azur Interior")]
    assert ft.calls[0][1] == "name_search"
