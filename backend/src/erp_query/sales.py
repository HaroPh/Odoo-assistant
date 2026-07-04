"""Sales bounded context — business read functions. Domains live here, in Python."""
from datetime import datetime, timezone

from .envelope import ok, err
from .gateway import default_gateway
from .resolve import resolve_entity


def _period_from(period):
    t = datetime.now(timezone.utc)
    if period == "month":
        return t.replace(day=1).strftime("%Y-%m-%d")
    if period == "quarter":
        return t.replace(month=((t.month - 1) // 3) * 3 + 1, day=1).strftime("%Y-%m-%d")
    if period == "year":
        return t.replace(month=1, day=1).strftime("%Y-%m-%d")
    return None


def find_customer(name, *, gw=None):
    return resolve_entity("res.partner", name, gw=gw)


def list_sale_orders(state=None, customer=None, date_from=None, date_to=None, limit=50, *, gw=None):
    gw = gw or default_gateway()
    domain = []
    if state:
        domain.append(["state", "=", state])
    if customer:
        domain.append(["partner_id.name", "ilike", customer])
    if date_from:
        domain.append(["date_order", ">=", date_from + " 00:00:00"])
    if date_to:
        domain.append(["date_order", "<=", date_to + " 23:59:59"])
    try:
        rows = gw.search_read("sale.order", domain,
                              ["name", "partner_id", "date_order", "state", "amount_total",
                               "delivery_status"], order="date_order desc", limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu đơn bán: {e}")
    if not rows:
        return ok({"rows": [], "count": 0}, "Không tìm thấy đơn bán nào phù hợp.")
    lines = [f"{r['name']} | {(r['partner_id'] or [0, 'N/A'])[1]} | {r['state']} "
             f"| {r['amount_total']:,.0f}" for r in rows]
    return ok({"rows": rows, "count": len(rows)},
              f"{len(rows)} đơn bán:\n" + "\n".join(lines))


def get_sale_order_detail(ref, *, gw=None):
    gw = gw or default_gateway()
    try:
        orders = gw.search_read("sale.order", [["name", "=", ref]],
                                ["id", "name", "partner_id", "amount_total", "state"], limit=2)
        if not orders:
            return err(f"Không tìm thấy đơn '{ref}'.")
        if len(orders) > 1:
            return err(f"Có nhiều đơn tên '{ref}'.")
        o = orders[0]
        lines = gw.search_read("sale.order.line", [["order_id", "=", o["id"]]],
                               ["product_id", "product_uom_qty", "price_unit", "price_subtotal"],
                               order="id asc", limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu chi tiết đơn: {e}")
    body = "\n".join(f"  {(l['product_id'] or [0, 'N/A'])[1]} | SL {l['product_uom_qty']:.1f} "
                     f"| {l['price_unit']:,.0f} | {l['price_subtotal']:,.0f}" for l in lines)
    return ok({"order": o, "lines": lines},
              f"Đơn {o['name']} | {(o['partner_id'] or [0, 'N/A'])[1]} "
              f"| Tổng {o['amount_total']:,.0f}\n{body}")


def get_product_price(product_id, partner_id=None, qty=1.0, *, gw=None):
    """Sell price = the product's `list_price` (giá niêm yết). Pricelist-applied
    pricing needs an ORM *method* (e.g. `_get_contextual_price`), which the
    read-only gateway does not permit, and Odoo 19 dropped the context-computed
    `price` field on product.product — so list_price is the gateway-readable
    sale price. `partner_id` is accepted for API stability but not used here."""
    gw = gw or default_gateway()
    try:
        rows = gw.search_read("product.product", [["id", "=", product_id]],
                              ["name", "list_price"], limit=1)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra giá: {e}")
    if not rows:
        return err(f"Không tìm thấy sản phẩm ID {product_id}.")
    price = rows[0].get("list_price") or 0.0
    return ok({"product_id": product_id, "name": rows[0].get("name"),
               "price": price, "qty": qty},
              f"Giá {rows[0].get('name')}: {price:,.0f} (SL {qty:g}).")


def sales_summary(period="month", *, gw=None):
    gw = gw or default_gateway()
    domain = [["state", "in", ["sale", "done"]]]
    df = _period_from(period)
    if df:
        domain.append(["date_order", ">=", df + " 00:00:00"])
    try:
        groups = gw.read_group("sale.order", domain,
                               ["amount_total:sum"], ["partner_id"], limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tổng hợp doanh thu: {e}")
    total = sum(g.get("amount_total") or 0 for g in groups)
    rows = [{"partner": (g.get("partner_id") or [0, "N/A"])[1],
             "amount": g.get("amount_total") or 0} for g in groups]
    rows.sort(key=lambda r: r["amount"], reverse=True)
    top = "\n".join(f"  {r['partner']}: {r['amount']:,.0f}" for r in rows[:5])
    return ok({"period": period, "total": total, "by_partner": rows},
              f"Doanh thu {period}: {total:,.0f}\nTop khách:\n{top}")


def top_products(by="quantity", period=None, limit=10, *, gw=None):
    gw = gw or default_gateway()
    domain = [["order_id.state", "in", ["sale", "done"]]]
    df = _period_from(period)
    if df:
        domain.append(["order_id.date_order", ">=", df + " 00:00:00"])
    orderby = "price_subtotal desc" if by == "revenue" else "product_uom_qty desc"
    try:
        groups = gw.read_group("sale.order.line", domain,
                               ["product_uom_qty:sum", "price_subtotal:sum"], ["product_id"],
                               orderby=orderby, limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi top sản phẩm: {e}")
    rows = [{"product": (g.get("product_id") or [0, "N/A"])[1],
             "qty": g.get("product_uom_qty") or 0,
             "revenue": g.get("price_subtotal") or 0} for g in groups]
    body = "\n".join(f"  {i}. {r['product']} | SL {r['qty']:,.0f} | DT {r['revenue']:,.0f}"
                     for i, r in enumerate(rows, 1))
    return ok({"by": by, "rows": rows}, f"Top {len(rows)} sản phẩm:\n{body}")
