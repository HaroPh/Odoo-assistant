import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query.gateway import Gateway
from backend.src.erp_query import inventory


class FakeTransport:
    def __init__(self, ret): self.ret = ret; self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs)); return self.ret


def _gw(rows): return Gateway(FakeTransport(rows))


def test_find_product_delegates_to_resolve():
    out = inventory.find_product("Tủ", gw=_gw([(552, "Tủ lớn [TU01]")]))
    assert out["data"]["matches"][0]["id"] == 552


def test_get_stock_builds_internal_domain_and_envelope():
    rows = [{"product_id": [552, "Tủ lớn"], "location_id": [8, "WH/Stock"],
             "quantity": 10.0, "reserved_quantity": 2.0, "available_quantity": 8.0,
             "product_uom_id": [1, "Cái"]}]
    gw = _gw(rows)
    out = inventory.get_stock(product="Tủ", gw=gw)
    assert out["data"]["count"] == 1
    model, method, args, kwargs = gw._t.calls[0]
    assert model == "stock.quant"
    assert ["location_id.usage", "=", "internal"] in args[0]


class MultiModelTransport:
    """Fake trả kết quả theo model — cho hàm gọi 2 model khác nhau."""
    def __init__(self, by_model): self.by_model = by_model; self.calls = []
    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs))
        return self.by_model[model]


OP_LAMP = {"id": 1, "product_id": [6, "Office Lamp"], "product_min_qty": 5.0,
           "product_max_qty": 10.0, "warehouse_id": [1, "WH"]}


def _reorder_gw(orderpoints, quant_groups):
    return Gateway(MultiModelTransport({
        "stock.warehouse.orderpoint": orderpoints,
        "stock.quant": quant_groups,
    }))


def test_reorder_below_threshold_suggests_up_to_max():
    # Không có quant group nào → on-hand mặc định 0 < min 5 → gợi ý mua max-0.
    gw = _reorder_gw([OP_LAMP], [])
    out = inventory.list_reorder_needed(gw=gw)
    assert out["status"] == "success"
    assert out["data"]["count"] == 1
    row = out["data"]["rows"][0]
    assert row["product_id"] == 6
    assert row["product_name"] == "Office Lamp"
    assert row["on_hand"] == 0.0
    assert row["min_qty"] == 5.0
    assert row["suggested_qty"] == 10.0
    assert row["warehouse"] == "WH"
    assert "Office Lamp" in out["display"]
    assert "gợi ý mua 10" in out["display"]


def test_reorder_none_below_threshold():
    groups = [{"product_id": [6, "Office Lamp"], "quantity": 8.0}]
    gw = _reorder_gw([OP_LAMP], groups)      # 8 >= min 5 → không dưới ngưỡng
    out = inventory.list_reorder_needed(gw=gw)
    assert out["status"] == "success"
    assert out["data"]["rows"] == [] and out["data"]["count"] == 0
    assert "Không có sản phẩm nào dưới mức tồn kho tối thiểu" in out["display"]


def test_reorder_no_orderpoints_short_circuits():
    gw = _reorder_gw([], [])
    out = inventory.list_reorder_needed(gw=gw)
    assert out["data"]["rows"] == []
    assert "Chưa có quy tắc tái đặt hàng" in out["display"]
    assert len(gw._t.calls) == 1             # KHÔNG gọi tiếp stock.quant


def test_reorder_degenerate_min_zero_rule_excluded():
    op = {"id": 4, "product_id": [9, "Table"], "product_min_qty": 0.0,
          "product_max_qty": 0.0, "warehouse_id": [1, "WH"]}
    gw = _reorder_gw([op], [])               # on-hand 0, min 0 → 0 < 0 là False
    out = inventory.list_reorder_needed(gw=gw)
    assert out["data"]["rows"] == []


def test_reorder_call_shapes_orderpoint_and_quant():
    # Bài học round 1 (product_tmpl_id): assert NỘI DUNG args, không chỉ đếm call.
    gw = _reorder_gw([OP_LAMP], [])
    inventory.list_reorder_needed(gw=gw)
    op_model, op_method, op_args, op_kwargs = gw._t.calls[0]
    assert op_model == "stock.warehouse.orderpoint" and op_method == "search_read"
    assert ["active", "=", True] in op_args[0]
    assert op_kwargs["limit"] == 100
    q_model, q_method, q_args, q_kwargs = gw._t.calls[1]
    assert q_model == "stock.quant" and q_method == "read_group"
    domain, fields, groupby = q_args
    assert ["product_id", "in", [6]] in domain
    assert ["location_id.usage", "=", "internal"] in domain
    assert fields == ["quantity:sum"]
    assert groupby == ["product_id"]


def test_reorder_gateway_error_on_orderpoint_returns_err():
    class Boom:
        def call(self, model, method, args, kwargs): raise RuntimeError("down")
    out = inventory.list_reorder_needed(gw=Gateway(Boom()))
    assert out["status"] == "error"
    assert "quy tắc tái đặt hàng" in out["error"]


def test_reorder_gateway_error_on_quant_returns_err():
    class BoomOnQuant:
        def call(self, model, method, args, kwargs):
            if model == "stock.quant":
                raise RuntimeError("down")
            return [OP_LAMP]
    out = inventory.list_reorder_needed(gw=Gateway(BoomOnQuant()))
    assert out["status"] == "error"
    assert "tồn kho" in out["error"]


def test_list_late_deliveries_default_both_directions():
    rows = [{"name": "WH/OUT/00001", "partner_id": [8, "Wood Corner"],
             "scheduled_date": "2026-06-23 13:28:37", "state": "assigned"}]
    gw = _gw(rows)
    out = inventory.list_late_deliveries(gw=gw)
    assert out["data"]["count"] == 1
    model, method, args, kwargs = gw._t.calls[0]
    assert model == "stock.picking"
    domain = args[0]
    assert ["picking_type_id.code", "in", ["outgoing", "incoming"]] in domain
    assert any(clause[0] == "scheduled_date" and clause[1] == "<" for clause in domain)


def test_list_late_deliveries_direction_filters_to_one_code():
    gw = _gw([])
    inventory.list_late_deliveries(direction="outgoing", gw=gw)
    domain = gw._t.calls[0][2][0]
    assert ["picking_type_id.code", "in", ["outgoing"]] in domain


def test_list_late_deliveries_empty_returns_zero_count():
    out = inventory.list_late_deliveries(gw=_gw([]))
    assert out["data"]["count"] == 0
    assert "Không có phiếu" in out["display"]


def test_list_late_deliveries_missing_partner_shows_dash():
    rows = [{"name": "WH/IN/00001", "partner_id": False,
             "scheduled_date": "2026-06-23 13:28:37", "state": "draft"}]
    out = inventory.list_late_deliveries(gw=_gw(rows))
    assert "—" in out["display"]


def test_list_late_deliveries_caps_display_at_15_rows():
    # Generate 20 fake delivery rows, sorted by date
    rows = [
        {"name": f"WH/OUT/{i:05d}", "partner_id": [i, f"Partner{i}"],
         "scheduled_date": f"2026-06-{10+i:02d} 10:00:00", "state": "assigned"}
        for i in range(20)
    ]
    out = inventory.list_late_deliveries(gw=_gw(rows))
    # Count should reflect true total, but data["rows"] capped at 15
    assert out["data"]["count"] == 20
    assert len(out["data"]["rows"]) == 15
    # Display should only show first 15 + truncation note
    display = out["display"]
    assert "20 phiếu trễ hạn:" in display
    # First 15 should appear
    for i in range(15):
        assert f"WH/OUT/{i:05d}" in display
    # 16-20 should NOT appear (they're not in the first 15)
    for i in range(15, 20):
        assert f"WH/OUT/{i:05d}" not in display
    # Truncation note should appear
    assert "...và 5 phiếu khác." in display


def test_list_late_deliveries_no_truncation_note_for_15_or_fewer():
    # Generate exactly 3 rows
    rows = [
        {"name": f"WH/OUT/{i:05d}", "partner_id": [i, f"Partner{i}"],
         "scheduled_date": f"2026-06-{20+i:02d} 10:00:00", "state": "assigned"}
        for i in range(3)
    ]
    out = inventory.list_late_deliveries(gw=_gw(rows))
    assert out["data"]["count"] == 3
    assert len(out["data"]["rows"]) == 3
    display = out["display"]
    # All 3 should appear
    for i in range(3):
        assert f"WH/OUT/{i:05d}" in display
    # NO truncation note should appear
    assert "...và" not in display


def test_list_late_deliveries_capped_false_for_small_result():
    rows = [{"name": "WH/OUT/00001", "partner_id": [8, "Wood Corner"],
             "scheduled_date": "2026-06-23 13:28:37", "state": "assigned"}]
    out = inventory.list_late_deliveries(gw=_gw(rows))
    assert out["data"]["capped"] is False


def test_list_late_deliveries_capped_true_at_100_rows():
    # Generate exactly 100 rows to simulate hitting the gateway limit
    rows = [
        {"name": f"WH/OUT/{i:05d}", "partner_id": [i, f"Partner{i}"],
         "scheduled_date": f"2026-05-{10 + (i % 20):02d} {10 + (i // 20):02d}:00:00", "state": "assigned"}
        for i in range(100)
    ]
    out = inventory.list_late_deliveries(gw=_gw(rows))
    assert out["data"]["count"] == 100
    assert len(out["data"]["rows"]) == 15  # data["rows"] capped at 15
    assert out["data"]["capped"] is True
    # Display should still only show first 15, plus truncation note
    display = out["display"]
    for i in range(15):
        assert f"WH/OUT/{i:05d}" in display
    assert "...và 85 phiếu khác." in display
    # Verify caveat text is present when capped=True
    assert "(có thể còn nhiều hơn — đã đạt giới hạn 100 dòng)" in display


def test_list_late_deliveries_capped_false_excludes_caveat():
    # Generate 50 rows (less than 100 limit) — capped should be False
    rows = [
        {"name": f"WH/OUT/{i:05d}", "partner_id": [i, f"Partner{i}"],
         "scheduled_date": f"2026-05-{10 + (i % 10):02d} {10 + (i // 10):02d}:00:00", "state": "assigned"}
        for i in range(50)
    ]
    out = inventory.list_late_deliveries(gw=_gw(rows))
    assert out["data"]["count"] == 50
    assert out["data"]["capped"] is False
    display = out["display"]
    # Caveat text should NOT appear when capped=False
    assert "(có thể còn nhiều hơn — đã đạt giới hạn 100 dòng)" not in display
