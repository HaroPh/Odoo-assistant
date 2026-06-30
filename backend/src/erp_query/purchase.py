"""Purchase bounded context — suppliers and purchase orders."""
from .envelope import ok, err
from .gateway import default_gateway
from .resolve import resolve_entity


def find_supplier(name, *, gw=None):
    return resolve_entity("res.partner", name, gw=gw)


def list_purchase_orders(state=None, vendor=None, date_from=None, date_to=None, limit=50, *, gw=None):
    gw = gw or default_gateway()
    domain = []
    if state:
        domain.append(["state", "=", state])
    if vendor:
        domain.append(["partner_id.name", "ilike", vendor])
    if date_from:
        domain.append(["date_order", ">=", date_from + " 00:00:00"])
    if date_to:
        domain.append(["date_order", "<=", date_to + " 23:59:59"])
    try:
        rows = gw.search_read("purchase.order", domain,
                              ["name", "partner_id", "date_order", "state", "amount_total"],
                              order="date_order desc", limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra đơn mua: {e}")
    if not rows:
        return ok({"rows": [], "count": 0}, "Không tìm thấy đơn mua nào phù hợp.")
    body = "\n".join(f"  {r['name']} | {(r['partner_id'] or [0, 'N/A'])[1]} | {r['state']} "
                     f"| {r['amount_total']:,.0f}" for r in rows)
    return ok({"rows": rows, "count": len(rows)}, f"{len(rows)} đơn mua:\n{body}")


def get_purchase_order_detail(ref, *, gw=None):
    gw = gw or default_gateway()
    try:
        orders = gw.search_read("purchase.order", [["name", "=", ref]],
                                ["id", "name", "partner_id", "amount_total"], limit=2)
        if not orders:
            return err(f"Không tìm thấy đơn mua '{ref}'.")
        if len(orders) > 1:
            return err(f"Có nhiều đơn mua tên '{ref}'.")
        o = orders[0]
        lines = gw.search_read("purchase.order.line", [["order_id", "=", o["id"]]],
                               ["product_id", "product_qty", "price_unit", "price_subtotal"],
                               order="id asc", limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra chi tiết đơn mua: {e}")
    body = "\n".join(f"  {(l['product_id'] or [0, 'N/A'])[1]} | SL {l['product_qty']:.1f} "
                     f"| {l['price_unit']:,.0f} | {l['price_subtotal']:,.0f}" for l in lines)
    return ok({"order": o, "lines": lines},
              f"Đơn mua {o['name']} | {(o['partner_id'] or [0, 'N/A'])[1]} "
              f"| Tổng {o['amount_total']:,.0f}\n{body}")
