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
