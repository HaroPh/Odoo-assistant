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
                                ["id", "name", "partner_id", "amount_total", "state"], limit=2)
        if not orders:
            return err(f"Không tìm thấy đơn mua '{ref}'.")
        if len(orders) > 1:
            return err(f"Có nhiều đơn mua tên '{ref}'.")
        o = orders[0]
        lines = gw.search_read("purchase.order.line", [["order_id", "=", o["id"]]],
                               ["id", "product_id", "product_qty", "price_unit", "price_subtotal"],
                               order="id asc", limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra chi tiết đơn mua: {e}")
    body = "\n".join(f"  {(l['product_id'] or [0, 'N/A'])[1]} | SL {l['product_qty']:.1f} "
                     f"| {l['price_unit']:,.0f} | {l['price_subtotal']:,.0f}" for l in lines)
    return ok({"order": o, "lines": lines},
              f"Đơn mua {o['name']} | {(o['partner_id'] or [0, 'N/A'])[1]} "
              f"| Tổng {o['amount_total']:,.0f}\n{body}")


def list_suppliers(limit=50, *, gw=None):
    gw = gw or default_gateway()
    try:
        rows = gw.search_read("res.partner", [["supplier_rank", ">", 0]],
                              ["name", "email", "phone", "city"],
                              order="name asc", limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu nhà cung cấp: {e}")
    if not rows:
        return ok({"rows": [], "count": 0}, "Chưa có nhà cung cấp nào trong hệ thống.")
    lines = [f"{r['name']} | {r['email'] or '—'} | {r['phone'] or '—'}" for r in rows]
    return ok({"rows": rows, "count": len(rows)},
              f"{len(rows)} nhà cung cấp:\n" + "\n".join(lines))


def _resolve_single(model, query, gw):
    """resolve_entity envelope -> (row_with_id_and_name, None) | (None, error_msg).
    Cùng logic với resolve_entity_for_order (backend/src/agents/create_order.py)
    nhưng viết lại tại đây — erp_query KHÔNG import từ backend/src/agents (chiều
    phụ thuộc ngược lại: agents phụ thuộc erp_query)."""
    env = resolve_entity(model, query, gw=gw)
    if env.get("status") != "success":
        return None, env.get("display") or "Lỗi tra cứu."
    data = env.get("data") or {}
    matches = data.get("matches") or []
    if not matches:
        return None, f"Không tìm thấy '{query}'."
    if data.get("needs_disambiguation"):
        names = "; ".join(f"{m['name']} (ID {m['id']})" for m in matches)
        return None, f"Có nhiều kết quả cho '{query}': {names}."
    exact = [m for m in matches if (m["name"] or "").strip().lower() == query.strip().lower()]
    chosen = exact[0] if exact else matches[0]
    return {"id": chosen["id"], "name": chosen["name"]}, None


def get_product_suppliers(product, *, gw=None):
    gw = gw or default_gateway()
    prod, msg = _resolve_single("product.product", product, gw)
    if msg:
        return err(msg)
    try:
        tmpl_rows = gw.search_read("product.product", [["id", "=", prod["id"]]],
                                   ["product_tmpl_id"], limit=1)
        tmpl_id = (tmpl_rows[0]["product_tmpl_id"][0]
                  if tmpl_rows and tmpl_rows[0].get("product_tmpl_id") else None)
        declared = []
        if tmpl_id:
            declared = gw.search_read("product.supplierinfo",
                                      [["product_tmpl_id", "=", tmpl_id]],
                                      ["partner_id", "price", "min_qty", "delay"],
                                      order="price asc", limit=20)
        history = gw.search_read("purchase.order.line",
                                 [["product_id", "=", prod["id"]],
                                  ["state", "in", ["purchase", "done"]]],
                                 ["partner_id"], limit=50)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu nhà cung cấp của sản phẩm: {e}")
    seen = set()
    history_partners = []
    for h in history:
        pid = h.get("partner_id")
        if pid and pid[0] not in seen:
            seen.add(pid[0])
            history_partners.append(pid[1])
    body = [f"Nhà cung cấp của {prod['name']}:"]
    if declared:
        body.append("NCC khai báo (bảng giá):")
        body += [f"  {d['partner_id'][1] if d['partner_id'] else '?'} — "
                f"{d['price']:,.0f}đ/đv, tối thiểu {d['min_qty']:g}, "
                f"giao trong {d['delay']} ngày" for d in declared]
    else:
        body.append("NCC khai báo (bảng giá): chưa có.")
    if history_partners:
        body.append("NCC đã nhập (theo đơn mua): " + ", ".join(history_partners))
    else:
        body.append("NCC đã nhập (theo đơn mua): chưa từng nhập.")
    return ok({"product": prod, "declared": declared,
              "history_partners": history_partners}, "\n".join(body))


def get_supplier_detail(name, *, gw=None):
    gw = gw or default_gateway()
    sup, msg = _resolve_single("res.partner", name, gw)
    if msg:
        return err(msg)
    try:
        rows = gw.search_read("res.partner", [["id", "=", sup["id"]]],
                              ["name", "email", "phone", "vat", "street", "city",
                               "bank_ids", "property_supplier_payment_term_id"],
                              limit=1)
        p = rows[0]
        banks = []
        if p.get("bank_ids"):
            banks = gw.search_read("res.partner.bank", [["id", "in", p["bank_ids"]]],
                                   ["acc_number", "bank_id"], limit=10)
        pos = gw.search_read("purchase.order", [["partner_id", "=", sup["id"]]],
                             ["id"], limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu hồ sơ nhà cung cấp: {e}")
    bank_txt = "; ".join(f"{b['acc_number']} ({b['bank_id'][1] if b['bank_id'] else '?'})"
                         for b in banks) or "—"
    term = p.get("property_supplier_payment_term_id")
    display = (f"Nhà cung cấp: {p['name']}\n"
              f"  Email: {p['email'] or '—'} | Điện thoại: {p['phone'] or '—'}\n"
              f"  Mã số thuế: {p['vat'] or '—'}\n"
              f"  Địa chỉ: {p['street'] or '—'}, {p['city'] or '—'}\n"
              f"  Ngân hàng: {bank_txt}\n"
              f"  Điều khoản thanh toán: {term[1] if term else '—'}\n"
              f"  Số đơn mua đã có: {len(pos)}")
    return ok({"partner": p, "bank_accounts": banks, "po_count": len(pos)}, display)


def check_po_matching(ref, *, gw=None):
    """Đối soát 1 PO theo mã: dòng nào đã xuất hóa đơn NHIỀU HƠN thực nhận
    (SOP 15 — kiểm tra trước khi confirm vendor bill). Nhận-chưa-hóa-đơn
    hoặc chưa-nhận-đủ là bình thường (đơn đang xử lý dở), KHÔNG tính là
    lệch."""
    gw = gw or default_gateway()
    try:
        pos = gw.search_read("purchase.order", [["name", "=", ref]],
                             ["id", "name", "partner_id"], limit=2)
        if not pos:
            return err(f"Không tìm thấy đơn mua '{ref}'.")
        if len(pos) > 1:
            return err(f"Có nhiều đơn mua tên '{ref}'.")
        po = pos[0]
        lines = gw.search_read("purchase.order.line", [["order_id", "=", po["id"]]],
                               ["product_id", "product_qty", "qty_received", "qty_invoiced"],
                               order="id asc", limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi đối soát đơn mua: {e}")
    if not lines:
        return err(f"Đơn mua '{ref}' không có dòng sản phẩm nào.")
    mismatches = [l for l in lines if l["qty_invoiced"] > l["qty_received"]]
    line_txt = "\n".join(
        f"  {(l['product_id'] or [0, 'N/A'])[1]} | đặt {l['product_qty']:g} "
        f"| nhận {l['qty_received']:g} | hóa đơn {l['qty_invoiced']:g}"
        + ("  ⚠ hóa đơn nhiều hơn thực nhận" if l["qty_invoiced"] > l["qty_received"] else "")
        for l in lines)
    header = (f"Đơn mua {po['name']} | {(po['partner_id'] or [0, 'N/A'])[1]}: "
              + (f"{len(mismatches)} dòng LỆCH (hóa đơn > thực nhận)"
                 if mismatches else "khớp — không có dòng nào hóa đơn vượt thực nhận"))
    return ok({"order": po, "lines": lines, "mismatch_count": len(mismatches)},
              f"{header}\n{line_txt}")


def list_po_mismatches(*, gw=None):
    """Mọi PO đang mở/đã xong (state purchase|done) có ít nhất 1 dòng hóa
    đơn NHIỀU HƠN thực nhận — cần rà soát trước khi thanh toán thêm. Đếm qua
    search_read limit 100 CÓ Ý THỨC: capped=True khi chạm trần → hiển thị
    cảnh báo (khác truncation ngầm — đây là đếm có cap)."""
    gw = gw or default_gateway()
    try:
        lines = gw.search_read("purchase.order.line",
                               [["order_id.state", "in", ["purchase", "done"]],
                                ["qty_invoiced", ">", 0]],
                               ["order_id", "product_id", "product_qty",
                                "qty_received", "qty_invoiced"],
                               order="order_id asc", limit=100)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra đơn mua lệch đối soát: {e}")
    capped = len(lines) >= 100
    bad = [l for l in lines if l["qty_invoiced"] > l["qty_received"]]
    if not bad:
        msg = "Không có đơn mua nào có hóa đơn vượt thực nhận."
        if capped:
            msg += " (có thể còn nhiều hơn — đã đạt giới hạn 100 dòng)"
        return ok({"rows": [], "count": 0, "capped": capped}, msg)
    seen_pos = {l["order_id"][0]: l["order_id"][1] for l in bad}
    lines_txt = "\n".join(
        f"  {l['order_id'][1]} | {(l['product_id'] or [0, 'N/A'])[1]} "
        f"| nhận {l['qty_received']:g} | hóa đơn {l['qty_invoiced']:g}"
        for l in bad)
    summary = f"{len(seen_pos)} đơn mua có dòng hóa đơn vượt thực nhận"
    if capped:
        summary += " (có thể còn nhiều hơn — đã đạt giới hạn 100 dòng)"
    return ok({"rows": bad, "count": len(seen_pos), "capped": capped},
              f"{summary}:\n{lines_txt}")
