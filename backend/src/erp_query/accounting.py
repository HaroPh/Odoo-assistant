"""Accounting bounded context — customer/vendor invoices."""
from datetime import datetime, timezone

from .envelope import ok, err
from .gateway import default_gateway

_FIELDS = ["name", "partner_id", "invoice_date", "invoice_date_due",
           "amount_total", "amount_residual", "payment_state"]


def _today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def list_invoices(move_type, partner=None, payment_state=None, limit=50, *, gw=None):
    gw = gw or default_gateway()
    domain = [["move_type", "=", move_type], ["state", "=", "posted"]]
    if partner:
        domain.append(["partner_id.name", "ilike", partner])
    if payment_state:
        domain.append(["payment_state", "=", payment_state])
    try:
        rows = gw.search_read("account.move", domain, _FIELDS,
                              order="invoice_date desc", limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra hóa đơn: {e}")
    if not rows:
        return ok({"rows": [], "count": 0}, "Không có hóa đơn nào phù hợp.")
    body = "\n".join(f"  {r['name']} | {(r['partner_id'] or [0, 'N/A'])[1]} "
                     f"| {r['amount_total']:,.0f} | còn {r['amount_residual']:,.0f} "
                     f"| {r['payment_state']}" for r in rows)
    return ok({"rows": rows, "count": len(rows)}, f"{len(rows)} hóa đơn:\n{body}")


def get_overdue_invoices(limit=50, *, gw=None):
    gw = gw or default_gateway()
    domain = [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
              ["payment_state", "in", ["not_paid", "partial"]],
              ["invoice_date_due", "<", _today()]]
    try:
        rows = gw.search_read("account.move", domain, _FIELDS,
                              order="invoice_date_due asc", limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra hóa đơn quá hạn: {e}")
    if not rows:
        return ok({"rows": [], "count": 0}, "Không có hóa đơn nào quá hạn.")
    body = "\n".join(f"  {r['name']} | {(r['partner_id'] or [0, 'N/A'])[1]} "
                     f"| đến hạn {r.get('invoice_date_due') or 'N/A'} "
                     f"| còn {r['amount_residual']:,.0f}" for r in rows)
    return ok({"rows": rows, "count": len(rows)},
              f"{len(rows)} hóa đơn quá hạn:\n{body}")


def get_partner_balance(name, *, gw=None):
    """Công nợ 1 đối tác cụ thể — CẢ hai chiều nếu có: phải thu (khách nợ
    mình, out_invoice) và phải trả (mình nợ NCC, in_invoice). KHÔNG cộng
    ròng — 2 loại sổ khác bản chất."""
    gw = gw or default_gateway()
    try:
        partners = gw.search_read("res.partner", [["name", "ilike", name]],
                                  ["id", "name"], limit=5)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu đối tác: {e}")
    if not partners:
        return err(f"Không tìm thấy đối tác '{name}'.")
    if len(partners) > 1:
        names = "; ".join(f"{p['name']} (ID {p['id']})" for p in partners)
        return err(f"Có nhiều đối tác khớp '{name}': {names}.")
    partner = partners[0]
    try:
        ar = gw.read_group("account.move",
                           [["move_type", "=", "out_invoice"], ["state", "=", "posted"],
                            ["payment_state", "in", ["not_paid", "partial"]],
                            ["partner_id", "=", partner["id"]]],
                           ["amount_residual:sum"], ["partner_id"])
        ap = gw.read_group("account.move",
                           [["move_type", "=", "in_invoice"], ["state", "=", "posted"],
                            ["payment_state", "in", ["not_paid", "partial"]],
                            ["partner_id", "=", partner["id"]]],
                           ["amount_residual:sum"], ["partner_id"])
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra công nợ: {e}")
    ar_amt = ar[0]["amount_residual"] if ar else 0.0
    ap_amt = ap[0]["amount_residual"] if ap else 0.0
    if not ar_amt and not ap_amt:
        return ok({"partner": partner, "receivable": 0.0, "payable": 0.0},
                  f"{partner['name']}: không còn công nợ nào.")
    parts = [f"{partner['name']}:"]
    if ar_amt:
        parts.append(f"  Khách nợ mình (phải thu): {ar_amt:,.0f}")
    if ap_amt:
        parts.append(f"  Mình nợ NCC (phải trả): {ap_amt:,.0f}")
    return ok({"partner": partner, "receivable": ar_amt, "payable": ap_amt},
              "\n".join(parts))


def find_posted_invoice(invoice_ref, *, gw=None):
    """NỘI BỘ (coordinator create_credit_memo) — resolve 1 hóa đơn khách
    theo SỐ CHÍNH XÁC, lọc move_type='out_invoice' + state='posted'. Phân
    biệt rõ 'không tồn tại' vs 'chưa phát hành' để báo lỗi đúng nguyên
    nhân."""
    gw = gw or default_gateway()
    try:
        rows = gw.search_read("account.move",
                              [["name", "=", invoice_ref],
                               ["move_type", "=", "out_invoice"]],
                              ["id", "name", "state", "partner_id",
                               "amount_total"], limit=2)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu hóa đơn: {e}")
    if not rows:
        return err(f"Không tìm thấy hóa đơn khách '{invoice_ref}'.")
    if len(rows) > 1:
        return err(f"Có nhiều hóa đơn tên '{invoice_ref}'.")
    inv = rows[0]
    if inv["state"] != "posted":
        return err(f"Hóa đơn {inv['name']} chưa phát hành "
                   f"(trạng thái: {inv['state']}).")
    return ok({"invoice": inv},
              f"Hóa đơn {inv['name']} | "
              f"{(inv['partner_id'] or [0, 'N/A'])[1]} | "
              f"{inv['amount_total']:,.0f}.")
