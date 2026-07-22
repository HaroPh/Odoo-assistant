import pytest
import security


def test_classify_known_methods():
    assert security.classify_operation("search_read") == "read"
    assert security.classify_operation("create") == "create"
    assert security.classify_operation("write") == "write"
    assert security.classify_operation("unlink") == "unlink"


def test_classify_unknown_method_denied():
    assert security.classify_operation("unlink_all_the_things") is None


def test_classify_case_and_whitespace_insensitive():
    assert security.classify_operation("  ACTION_CONFIRM  ") == "write"


def test_sanitize_model_accepts_valid_names():
    assert security.sanitize_model("sale.order") == "sale.order"
    assert security.sanitize_model("product.product") == "product.product"


def test_sanitize_model_rejects_injection():
    with pytest.raises(ValueError):
        security.sanitize_model("sale.order; DROP TABLE x")


def test_sanitize_model_rejects_empty():
    with pytest.raises(ValueError):
        security.sanitize_model("")


def test_sanitize_model_rejects_leading_trailing_whitespace():
    with pytest.raises(ValueError):
        security.sanitize_model(" product.product ")
    with pytest.raises(ValueError):
        security.sanitize_model("  sale.order")
    with pytest.raises(ValueError):
        security.sanitize_model("account.invoice  ")


def test_classify_register_payment_methods():
    assert security.classify_operation("action_register_payment") == "write"
    assert security.classify_operation("action_create_payments") == "write"


def test_classify_convert_opportunity_method():
    assert security.classify_operation("convert_opportunity") == "write"


def test_classify_scrap_validate_method():
    assert security.classify_operation("action_validate") == "write"


def test_classify_return_order_method():
    assert security.classify_operation("action_create_returns") == "write"


def test_classify_credit_memo_method():
    assert security.classify_operation("refund_moves") == "write"
