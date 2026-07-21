"""Inventory bounded context — product, stock, lots."""
from datetime import datetime, timezone
from .envelope import ok, err
from .gateway import default_gateway
from .resolve import resolve_entity


def find_product(name_or_code, *, gw=None):
    return resolve_entity("product.product", name_or_code, gw=gw)


def get_stock(product=None, limit=100, *, gw=None):
    gw = gw or default_gateway()
    domain = [["location_id.usage", "=", "internal"]]
    if product:
        domain.append(["product_id.name", "ilike", product])
    try:
        rows = gw.search_read("stock.quant", domain,
                              ["product_id", "location_id", "quantity",
                               "reserved_quantity", "available_quantity", "product_uom_id"],
                              order="product_id asc", limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra tồn kho: {e}")
    if not rows:
        return ok({"rows": [], "count": 0}, "Không có dữ liệu tồn kho phù hợp.")
    body = "\n".join(f"  {(r['product_id'] or [0, 'N/A'])[1]} @ {(r['location_id'] or [0, 'N/A'])[1]} "
                     f"| Có {r['available_quantity']:.1f} (tổng {r['quantity']:.1f}, "
                     f"giữ {r['reserved_quantity']:.1f})" for r in rows)
    return ok({"rows": rows, "count": len(rows)}, f"Tồn kho ({len(rows)} dòng):\n{body}")


def get_lots(product=None, limit=50, *, gw=None):
    gw = gw or default_gateway()
    domain = []
    if product:
        domain.append(["product_id.name", "ilike", product])
    try:
        rows = gw.search_read("stock.lot", domain,
                              ["name", "product_id", "product_qty"],
                              order="product_id asc", limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra lô/sê-ri: {e}")
    if not rows:
        return ok({"rows": [], "count": 0}, "Không tìm thấy lô/sê-ri phù hợp.")
    body = "\n".join(f"  {r['name']} | {(r['product_id'] or [0, 'N/A'])[1]} "
                     f"| {r['product_qty']:.1f}" for r in rows)
    return ok({"rows": rows, "count": len(rows)}, f"{len(rows)} lô/sê-ri:\n{body}")


def list_reorder_needed(*, gw=None):
    """Sản phẩm đang dưới mức tồn kho tối thiểu (theo Reordering Rules đã
    thiết lập trong Odoo, KHÔNG tự nghĩ ra ngưỡng mới) + số lượng gợi ý mua
    thêm (bù tới mức tối đa). Tồn thực tế sum qua read_group stock.quant —
    field qty_to_order của orderpoint chỉ cập nhật khi cron Run Scheduler
    chạy nên có thể stale, không dùng."""
    gw = gw or default_gateway()
    try:
        orderpoints = gw.search_read("stock.warehouse.orderpoint",
                                     [["active", "=", True]],
                                     ["product_id", "product_min_qty",
                                      "product_max_qty", "warehouse_id"],
                                     limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu quy tắc tái đặt hàng: {e}")
    if not orderpoints:
        return ok({"rows": [], "count": 0},
                  "Chưa có quy tắc tái đặt hàng nào được thiết lập.")

    pids = [op["product_id"][0] for op in orderpoints]
    try:
        groups = gw.read_group("stock.quant",
                               [["product_id", "in", pids],
                                ["location_id.usage", "=", "internal"]],
                               ["quantity:sum"], ["product_id"])
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu tồn kho: {e}")
    on_hand_by_pid = {(g.get("product_id") or [0, ""])[0]: g.get("quantity") or 0.0
                      for g in groups}

    below = []
    for op in orderpoints:
        pid = op["product_id"][0]
        on_hand = on_hand_by_pid.get(pid, 0.0)
        if on_hand < op["product_min_qty"]:
            below.append({
                "product_id": pid,
                "product_name": op["product_id"][1],
                "on_hand": on_hand,
                "min_qty": op["product_min_qty"],
                "max_qty": op["product_max_qty"],
                "suggested_qty": max(op["product_max_qty"] - on_hand, 0.0),
                "warehouse": op["warehouse_id"][1] if op.get("warehouse_id") else None,
            })
    if not below:
        return ok({"rows": [], "count": 0},
                  "Không có sản phẩm nào dưới mức tồn kho tối thiểu.")
    lines = [f"  {r['product_name']} | tồn {r['on_hand']:g} (min {r['min_qty']:g}) "
             f"| gợi ý mua {r['suggested_qty']:g}" for r in below]
    return ok({"rows": below, "count": len(below)},
              f"{len(below)} sản phẩm dưới mức tồn kho tối thiểu:\n" + "\n".join(lines))


def list_late_deliveries(direction=None, *, gw=None):
    """Phiếu giao/nhận đang trễ hạn (scheduled_date đã qua, chưa done/cancel).
    direction: "outgoing" (giao khách) | "incoming" (nhận từ NCC) | None
    (cả hai). Loại 'mrp_operation' (di chuyển nội bộ sản xuất, không phải
    giao/nhận với bên ngoài)."""
    gw = gw or default_gateway()
    codes = [direction] if direction in ("outgoing", "incoming") else ["outgoing", "incoming"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    domain = [["picking_type_id.code", "in", codes],
              ["state", "not in", ["done", "cancel"]],
              ["scheduled_date", "<", now]]
    try:
        rows = gw.search_read("stock.picking", domain,
                              ["name", "partner_id", "scheduled_date", "state"],
                              order="scheduled_date asc", limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra phiếu giao/nhận trễ hạn: {e}")
    capped = len(rows) >= 100
    if not rows:
        msg = "Không có phiếu giao/nhận nào trễ hạn."
        if capped:
            msg += " (có thể còn nhiều hơn — đã đạt giới hạn 100 dòng)"
        return ok({"rows": [], "count": 0, "capped": capped}, msg)
    # Cap both display and data["rows"] to first 15 for LLM token efficiency;
    # keep data["count"] as true total (may be > len(data["rows"]) when capped)
    display_rows = rows[:15]
    body = "\n".join(
        f"  {r['name']} | {(r['partner_id'] or [0, '—'])[1]} "
        f"| hẹn {r['scheduled_date'][:10]} | {r['state']}"
        for r in display_rows)
    count = len(rows)
    display_text = f"{count} phiếu trễ hạn"
    if capped:
        display_text += " (có thể còn nhiều hơn — đã đạt giới hạn 100 dòng)"
    display_text += f":\n{body}"
    if count > 15:
        display_text += f"\n...và {count - 15} phiếu khác."
    return ok({"rows": display_rows, "count": count, "capped": capped}, display_text)
