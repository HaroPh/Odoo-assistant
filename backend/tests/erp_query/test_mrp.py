import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query.gateway import Gateway
from backend.src.erp_query import mrp


class MultiModelTransport:
    """Fake theo model; giá trị list-of-lists sẽ pop lần lượt cho các lần gọi
    lặp lại cùng model (vd mrp.bom đọc 2 lần)."""
    def __init__(self, by_model):
        self.by_model = by_model
        self.calls = []

    def call(self, model, method, args, kwargs):
        self.calls.append((model, method, args, kwargs))
        v = self.by_model[model]
        if isinstance(v, list) and v and isinstance(v[0], list):
            return v.pop(0)
        return v


def _gw(by_model):
    return Gateway(MultiModelTransport(by_model))


_VARIANT = [{"id": 39, "name": "[FURN_8855] Drawer",
             "product_tmpl_id": [24, "[FURN_8855] Drawer"]}]


def test_find_boms_uses_template_id_from_variant():
    gw = _gw({"product.product": _VARIANT,
              "mrp.bom": [{"id": 7, "code": "PRIM-ASSEM", "type": "normal",
                           "product_qty": 1.0}]})
    out = mrp.find_boms_for_variant(39, gw=gw)
    assert out["status"] == "success"
    assert out["data"]["product"]["tmpl_id"] == 24
    assert out["data"]["boms"][0]["code"] == "PRIM-ASSEM"
    bom_call = next(c for c in gw._t.calls if c[0] == "mrp.bom")
    # Bẫy id-space: domain BoM phải dùng TEMPLATE 24, không phải variant 39
    assert ["product_tmpl_id", "=", 24] in bom_call[2][0]
    assert ["active", "=", True] in bom_call[2][0]


def test_find_boms_product_missing_err():
    gw = _gw({"product.product": []})
    out = mrp.find_boms_for_variant(999, gw=gw)
    assert out["status"] == "error"


def test_availability_math_batch_one():
    # mrp.bom và mrp.bom.line là 2 model riêng — mỗi model 1 entry trong fake.
    gw = _gw({"mrp.bom": [{"id": 7, "product_qty": 1.0}],
              "mrp.bom.line": [{"id": 1, "product_id": [67, "Drawer Black"],
                                "product_qty": 1.0}],
              "stock.quant": [{"product_id": [67, "Drawer Black"],
                               "quantity": 41.0}]})
    out = mrp.check_bom_availability(7, 2.0, gw=gw)
    assert out["status"] == "success"
    row = out["data"]["rows"][0]
    assert row["need"] == 2.0 and row["on_hand"] == 41.0 and row["enough"]
    assert out["data"]["all_enough"] is True


def test_availability_math_batch_scaling():
    # Batch 100 (Lab 5 style), sản xuất 50 → need = line_qty × 0.5
    gw = _gw({"mrp.bom": [{"id": 9, "product_qty": 100.0}],
              "mrp.bom.line": [{"id": 1, "product_id": [70, "Vải"],
                                "product_qty": 200.0}],
              "stock.quant": [{"product_id": [70, "Vải"], "quantity": 90.0}]})
    out = mrp.check_bom_availability(9, 50.0, gw=gw)
    row = out["data"]["rows"][0]
    assert row["need"] == 100.0            # 200 × 50/100
    assert row["enough"] is False          # 90 < 100
    assert out["data"]["all_enough"] is False


def test_availability_read_group_shape():
    gw = _gw({"mrp.bom": [{"id": 7, "product_qty": 1.0}],
              "mrp.bom.line": [{"id": 1, "product_id": [67, "X"],
                                "product_qty": 1.0}],
              "stock.quant": []})
    mrp.check_bom_availability(7, 2.0, gw=gw)
    q = next(c for c in gw._t.calls if c[0] == "stock.quant")
    assert q[1] == "read_group"
    domain, fields, groupby = q[2]
    assert ["product_id", "in", [67]] in domain
    assert ["location_id.usage", "=", "internal"] in domain
    assert fields == ["quantity:sum"] and groupby == ["product_id"]


def test_availability_gateway_error():
    class Boom:
        def call(self, *a): raise RuntimeError("down")
    out = mrp.check_bom_availability(7, 2.0, gw=Gateway(Boom()))
    assert out["status"] == "error"


def test_get_bom_detail_display_with_kit_label(monkeypatch):
    monkeypatch.setattr(mrp, "_resolve_product",
                        lambda name, gw=None: ({"id": 39, "name": "Drawer"}, None))
    gw = _gw({"product.product": _VARIANT,
              "mrp.bom": [{"id": 7, "code": "PRIM-ASSEM", "type": "normal",
                           "product_qty": 1.0},
                          {"id": 6, "code": None, "type": "phantom",
                           "product_qty": 1.0}],
              "mrp.bom.line": [{"id": 1, "bom_id": [7, "x"],
                                "product_id": [67, "Drawer Black"],
                                "product_qty": 1.0},
                               {"id": 2, "bom_id": [6, "x"],
                                "product_id": [68, "Case"],
                                "product_qty": 2.0}],
              "stock.quant": [{"product_id": [67, "Drawer Black"],
                               "quantity": 41.0}]})
    out = mrp.get_bom_detail("Drawer", gw=gw)
    assert out["status"] == "success"
    assert "PRIM-ASSEM" in out["display"]
    assert "Kit (không sản xuất trực tiếp)" in out["display"]
    assert "tồn 41" in out["display"]


def test_get_bom_detail_unknown_product_err(monkeypatch):
    from backend.src.erp_query.envelope import err
    monkeypatch.setattr(mrp, "_resolve_product",
                        lambda name, gw=None: (None, err("Không tìm thấy sản phẩm 'xyz'.")))
    out = mrp.get_bom_detail("xyz", gw=_gw({}))
    assert out["status"] == "error"


def test_list_mo_filters_state_and_product():
    gw = _gw({"mrp.production": [{"id": 7, "name": "WH/MO/00007",
                                  "product_id": [39, "Drawer"],
                                  "product_qty": 2.0, "qty_producing": 0.0,
                                  "state": "confirmed",
                                  "date_start": "2026-07-19 15:00:00",
                                  "origin": "AI Agent"}]})
    out = mrp.list_manufacturing_orders(state="confirmed", product="Drawer", gw=gw)
    assert out["data"]["count"] == 1
    assert "đã xác nhận" in out["display"]          # nhãn VN
    call = gw._t.calls[0]
    assert ["state", "=", "confirmed"] in call[2][0]
    assert ["product_id.name", "ilike", "Drawer"] in call[2][0]


def test_list_mo_empty():
    gw = _gw({"mrp.production": []})
    out = mrp.list_manufacturing_orders(gw=gw)
    assert out["data"]["rows"] == []
    assert "Không có lệnh sản xuất" in out["display"]


def test_get_bom_recipe_shape():
    gw = _gw({"mrp.bom": [{"id": 9, "code": "AI-BOM", "type": "normal",
                           "product_qty": 2.0}],
              "mrp.bom.line": [{"id": 18, "product_id": [67, "Drawer Black"],
                                "product_qty": 5.0},
                               {"id": 19, "product_id": [68, "Case"],
                                "product_qty": 1.0}]})
    out = mrp.get_bom_recipe(9, gw=gw)
    assert out["status"] == "success"
    assert out["data"]["bom"]["code"] == "AI-BOM"
    assert out["data"]["bom"]["product_qty"] == 2.0
    assert out["data"]["lines"][0] == {"product_id": 67, "name": "Drawer Black",
                                       "qty": 5.0}
    line_call = next(c for c in gw._t.calls if c[0] == "mrp.bom.line")
    assert ["bom_id", "=", 9] in line_call[2][0]


def test_get_bom_recipe_not_found():
    gw = _gw({"mrp.bom": []})
    out = mrp.get_bom_recipe(999, gw=gw)
    assert out["status"] == "error" and "Không tìm thấy BoM" in out["error"]


def test_open_mo_count_domain_and_value():
    gw = _gw({"mrp.production": [{"id": 12}, {"id": 13}]})
    out = mrp.open_mo_count_for_bom(9, gw=gw)
    assert out["status"] == "success"
    assert out["data"]["count"] == 2 and out["data"]["capped"] is False
    call = gw._t.calls[0]
    assert ["bom_id", "=", 9] in call[2][0]
    assert ["state", "in", ["draft", "confirmed", "progress", "to_close"]] in call[2][0]


def test_open_mo_count_capped_at_100():
    gw = _gw({"mrp.production": [{"id": i} for i in range(100)]})
    out = mrp.open_mo_count_for_bom(9, gw=gw)
    assert out["data"]["count"] == 100 and out["data"]["capped"] is True


def test_pending_kit_orders_counts_undelivered_only():
    gw = _gw({
        "mrp.bom": [{"id": 6, "product_tmpl_id": [48, "Table Kit"]}],
        "product.product": [{"id": 66}],
        "sale.order.line": [{"id": 1, "order_id": [119, "S00119"]},
                            {"id": 2, "order_id": [120, "S00120"]}],
        "sale.order": [{"id": 119, "picking_ids": [154]},
                       {"id": 120, "picking_ids": [155]}],
        "stock.picking": [{"id": 154, "state": "assigned"},
                          {"id": 155, "state": "done"}],
    })
    out = mrp.count_pending_sale_orders_for_kit(6, gw=gw)
    assert out["status"] == "success"
    assert out["data"]["count"] == 1          # only order 119 (picking not done)
    assert out["data"]["capped"] is False


def test_pending_kit_orders_bom_not_found():
    gw = _gw({"mrp.bom": []})
    out = mrp.count_pending_sale_orders_for_kit(999, gw=gw)
    assert out["status"] == "error"


def test_pending_kit_orders_zero_when_no_matching_lines():
    gw = _gw({
        "mrp.bom": [{"id": 6, "product_tmpl_id": [48, "Table Kit"]}],
        "product.product": [{"id": 66}],
        "sale.order.line": [],
    })
    out = mrp.count_pending_sale_orders_for_kit(6, gw=gw)
    assert out["status"] == "success"
    assert out["data"]["count"] == 0 and out["data"]["capped"] is False


def test_pending_kit_orders_capped_at_100():
    gw = _gw({
        "mrp.bom": [{"id": 6, "product_tmpl_id": [48, "Table Kit"]}],
        "product.product": [{"id": 66}],
        "sale.order.line": [{"id": i, "order_id": [1000 + i, f"S{1000+i}"]}
                            for i in range(100)],
        "sale.order": [{"id": 1000 + i, "picking_ids": [2000 + i]}
                       for i in range(100)],
        "stock.picking": [{"id": 2000 + i, "state": "assigned"}
                          for i in range(100)],
    })
    out = mrp.count_pending_sale_orders_for_kit(6, gw=gw)
    assert out["status"] == "success"
    assert out["data"]["capped"] is True
