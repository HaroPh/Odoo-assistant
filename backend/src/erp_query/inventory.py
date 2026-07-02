"""Inventory bounded context — product, stock, lots."""
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
