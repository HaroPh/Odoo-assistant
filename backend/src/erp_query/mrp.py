"""Manufacturing bounded context — BoM (đọc) + Manufacturing Order (đọc).
Ghi (create/confirm/complete MO) nằm ở mcp-servers/odoo — file này CHỈ đọc.
Bẫy id-space (probe 2026-07-19): mrp.bom link theo product_tmpl_id (TEMPLATE),
mrp.production.product_id là VARIANT — trên instance thật template 39 =
"Table Top" nhưng variant 39 = "Drawer"; mọi lookup BoM phải đi qua
product.product.product_tmpl_id."""
from .envelope import ok, err
from .gateway import default_gateway
from .resolve import resolve_entity

_STATE_LABELS = {"draft": "nháp", "confirmed": "đã xác nhận",
                 "progress": "đang sản xuất", "to_close": "chờ đóng",
                 "done": "hoàn tất", "cancel": "đã hủy"}


def _resolve_product(name_or_code, *, gw=None):
    """(row {'id','name'} | None, err_envelope | None) — mirror cách
    purchase.py tự resolve cục bộ (tiền lệ round 1: không import chéo
    bounded context)."""
    env = resolve_entity("product.product", name_or_code, gw=gw)
    if env.get("status") != "success":
        return None, env
    data = env.get("data") or {}
    matches = data.get("matches") or []
    if not matches:
        return None, err(f"Không tìm thấy sản phẩm '{name_or_code}'.")
    if data.get("needs_disambiguation"):
        listing = "; ".join(m["name"] for m in matches[:5])
        return None, err(f"Có nhiều sản phẩm khớp '{name_or_code}': {listing}. "
                         f"Vui lòng nêu rõ hơn.")
    return matches[0], None


def find_boms_for_variant(product_id, *, gw=None):
    """NỘI BỘ (coordinator + get_bom_detail) — không expose làm tool.
    Trả mọi type BoM active; caller tự lọc normal/phantom."""
    gw = gw or default_gateway()
    try:
        prows = gw.search_read("product.product", [["id", "=", product_id]],
                               ["id", "name", "product_tmpl_id"], limit=1)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu sản phẩm: {e}")
    if not prows:
        return err(f"Không tìm thấy sản phẩm ID {product_id}.")
    tmpl_id = prows[0]["product_tmpl_id"][0]
    try:
        boms = gw.search_read("mrp.bom",
                              [["product_tmpl_id", "=", tmpl_id],
                               ["active", "=", True]],
                              ["id", "code", "type", "product_qty"], limit=20)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu định mức: {e}")
    return ok({"product": {"id": prows[0]["id"], "name": prows[0]["name"],
                           "tmpl_id": tmpl_id},
               "boms": [{"id": b["id"], "code": b.get("code") or None,
                         "type": b["type"], "product_qty": b["product_qty"]}
                        for b in boms]},
              f"{len(boms)} BoM.")


def check_bom_availability(bom_id, mo_qty, *, gw=None):
    """NỘI BỘ — need = line_qty × mo_qty / batch. Tồn sum qua read_group
    server-side (KHÔNG search_read+tự-sum: Gateway ép limit → cắt dòng ngầm)."""
    gw = gw or default_gateway()
    try:
        brows = gw.search_read("mrp.bom", [["id", "=", bom_id]],
                               ["id", "product_qty"], limit=1)
        if not brows:
            return err(f"Không tìm thấy BoM {bom_id}.")
        batch = brows[0]["product_qty"] or 1.0
        lines = gw.search_read("mrp.bom.line", [["bom_id", "=", bom_id]],
                               ["product_id", "product_qty"], limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu định mức: {e}")
    if not lines:
        return ok({"rows": [], "all_enough": True}, "BoM không có nguyên liệu.")
    pids = [l["product_id"][0] for l in lines]
    try:
        groups = gw.read_group("stock.quant",
                               [["product_id", "in", pids],
                                ["location_id.usage", "=", "internal"]],
                               ["quantity:sum"], ["product_id"])
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu tồn kho: {e}")
    on_hand = {(g.get("product_id") or [0, ""])[0]: g.get("quantity") or 0.0
               for g in groups}
    rows = []
    for l in lines:
        pid = l["product_id"][0]
        need = l["product_qty"] * mo_qty / batch
        have = on_hand.get(pid, 0.0)
        rows.append({"product_id": pid, "name": l["product_id"][1],
                     "need": need, "on_hand": have, "enough": have >= need})
    return ok({"rows": rows, "all_enough": all(r["enough"] for r in rows)},
              f"{len(rows)} nguyên liệu.")


def get_bom_detail(product, *, gw=None):
    gw = gw or default_gateway()
    row, e = _resolve_product(product, gw=gw)
    if e is not None:
        return e
    benv = find_boms_for_variant(row["id"], gw=gw)
    if benv.get("status") != "success":
        return benv
    boms = benv["data"]["boms"]
    if not boms:
        return ok({"product": benv["data"]["product"], "boms": []},
                  f"Sản phẩm '{row['name']}' chưa có định mức (BoM).")
    try:
        all_lines = gw.search_read("mrp.bom.line",
                                   [["bom_id", "in", [b["id"] for b in boms]]],
                                   ["bom_id", "product_id", "product_qty"],
                                   limit=100)
    except Exception as ex:                                 # noqa: BLE001
        return err(f"Lỗi tra cứu định mức: {ex}")
    pids = sorted({l["product_id"][0] for l in all_lines})
    on_hand = {}
    if pids:
        try:
            groups = gw.read_group("stock.quant",
                                   [["product_id", "in", pids],
                                    ["location_id.usage", "=", "internal"]],
                                   ["quantity:sum"], ["product_id"])
            on_hand = {(g.get("product_id") or [0, ""])[0]:
                       g.get("quantity") or 0.0 for g in groups}
        except Exception:                                   # noqa: BLE001
            on_hand = {}    # tồn chỉ để tham khảo — lỗi không chặn hiển thị BoM
    out_boms = []
    parts = [f"Định mức của {row['name']}:"]
    for b in boms:
        label = b["code"] or f"BoM #{b['id']}"
        kind = " — Kit (không sản xuất trực tiếp)" if b["type"] == "phantom" else ""
        parts.append(f"BoM {label} — cho {b['product_qty']:g} đơn vị{kind}:")
        rows = []
        for l in (x for x in all_lines if x["bom_id"][0] == b["id"]):
            pid = l["product_id"][0]
            rows.append({"product_id": pid, "name": l["product_id"][1],
                         "qty": l["product_qty"], "on_hand": on_hand.get(pid, 0.0)})
            parts.append(f"  - {l['product_id'][1]} × {l['product_qty']:g} "
                         f"(tồn {on_hand.get(pid, 0.0):g})")
        out_boms.append({**b, "lines": rows})
    return ok({"product": benv["data"]["product"], "boms": out_boms},
              "\n".join(parts))


def list_manufacturing_orders(state=None, product=None, limit=20, *, gw=None):
    gw = gw or default_gateway()
    domain = []
    if state:
        domain.append(["state", "=", state])
    if product:
        domain.append(["product_id.name", "ilike", product])
    try:
        rows = gw.search_read("mrp.production", domain,
                              ["name", "product_id", "product_qty",
                               "qty_producing", "state", "date_start", "origin"],
                              order="id desc", limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu lệnh sản xuất: {e}")
    if not rows:
        return ok({"rows": [], "count": 0}, "Không có lệnh sản xuất nào phù hợp.")
    body = "\n".join(
        f"  {r['name']} | {(r['product_id'] or [0, 'N/A'])[1]} × "
        f"{r['product_qty']:g} | {_STATE_LABELS.get(r['state'], r['state'])}"
        for r in rows)
    return ok({"rows": rows, "count": len(rows)},
              f"{len(rows)} lệnh sản xuất:\n{body}")


def get_bom_recipe(bom_id, *, gw=None):
    """NỘI BỘ (coordinator update_bom dựng diff) — recipe hiện tại của 1 BoM."""
    gw = gw or default_gateway()
    try:
        brows = gw.search_read("mrp.bom", [["id", "=", bom_id]],
                               ["id", "code", "type", "product_qty"], limit=1)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu định mức: {e}")
    if not brows:
        return err(f"Không tìm thấy BoM {bom_id}.")
    try:
        lines = gw.search_read("mrp.bom.line", [["bom_id", "=", bom_id]],
                               ["product_id", "product_qty"], limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu định mức: {e}")
    b = brows[0]
    return ok({"bom": {"id": b["id"], "code": b.get("code") or None,
                       "type": b["type"], "product_qty": b["product_qty"]},
               "lines": [{"product_id": l["product_id"][0],
                          "name": l["product_id"][1], "qty": l["product_qty"]}
                         for l in lines]},
              f"{len(lines)} nguyên liệu.")


def open_mo_count_for_bom(bom_id, *, gw=None):
    """NỘI BỘ (coordinator update_bom cảnh báo blast-radius) — số MO đang mở
    trỏ vào BoM này. Đếm qua search_read limit 100 CÓ Ý THỨC: capped=True khi
    chạm trần → hiển thị '100+' (khác truncation ngầm — đây là đếm có cap)."""
    gw = gw or default_gateway()
    try:
        rows = gw.search_read("mrp.production",
                              [["bom_id", "=", bom_id],
                               ["state", "in", ["draft", "confirmed",
                                                "progress", "to_close"]]],
                              ["id"], limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu lệnh sản xuất: {e}")
    return ok({"count": len(rows), "capped": len(rows) >= 100},
              f"{len(rows)} lệnh sản xuất đang mở.")
