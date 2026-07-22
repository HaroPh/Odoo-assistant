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


def test_sanitize_payload_keys_accepts_flat_dict():
    security.sanitize_payload_keys({"partner_id": 5, "amount": 100.0})  # no raise


def test_sanitize_payload_keys_accepts_realistic_vals_shapes():
    # order_line one2many command-tuple, nested dict, matches create_quotation's
    # actual shape (server.py create_quotation) — must NOT be rejected.
    security.sanitize_payload_keys({
        "partner_id": 5,
        "order_line": [(0, 0, {"product_id": 3, "product_uom_qty": 2.0,
                               "price_unit": 100.0})],
    })  # no raise


def test_sanitize_payload_keys_accepts_context_and_search_modifiers():
    security.sanitize_payload_keys({"fields": ["id", "name"], "limit": 6,
                                    "order": "id asc"})  # no raise
    security.sanitize_payload_keys({"context": {"active_ids": [1, 2],
                                               "default_journal_id": 3}})  # no raise


def test_sanitize_payload_keys_accepts_empty_dict():
    security.sanitize_payload_keys({})  # no raise


def test_sanitize_payload_keys_accepts_list_and_none():
    security.sanitize_payload_keys([])  # no raise
    security.sanitize_payload_keys(None)  # no raise
    security.sanitize_payload_keys([[["id", "=", 1]]])  # domain shape, no raise


def test_sanitize_payload_keys_rejects_bad_key_shape():
    with pytest.raises(ValueError):
        security.sanitize_payload_keys({"totally bad key!!": 1})


def test_sanitize_payload_keys_rejects_uppercase_key():
    with pytest.raises(ValueError):
        security.sanitize_payload_keys({"Partner_Id": 5})


def test_sanitize_payload_keys_rejects_nested_bad_key():
    with pytest.raises(ValueError):
        security.sanitize_payload_keys({
            "order_line": [(0, 0, {"product_id": 3, "bad key!": 1})],
        })


def test_sanitize_payload_keys_rejects_non_string_key():
    with pytest.raises(ValueError):
        security.sanitize_payload_keys({1: "value"})


def test_sanitize_payload_keys_depth_cap():
    nested = {"a": 1}
    for _ in range(15):
        nested = {"a": [nested]}
    with pytest.raises(ValueError):
        security.sanitize_payload_keys(nested)
