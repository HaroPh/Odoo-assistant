"""
Odoo MCP Server — Phase 2: Write/Do-tools only
Reads moved to backend/src/erp_query/ (Tasks 1–8).

Transport: HTTP/SSE tại port 8001
Connect:   http://mcp-odoo:8001/sse  (từ backend container)
"""
import sys
import time
import xmlrpc.client
from mcp.server.fastmcp import FastMCP

from config import ODOO_URL, ODOO_DB, ODOO_USER, ODOO_PWD
from security import ODOO_METHOD_OPERATION_MAP, classify_operation, sanitize_model
from rate_limit import check_rate_limit
from event_log import log_mcp_event
from helpers import now_iso, today_iso, resolve_unique, envelope

mcp = FastMCP("odoo-mcp", host="0.0.0.0", port=8001)

# ─── Odoo connection ──────────────────────────────────────────────────────────

_uid: int | None = None

def get_uid() -> int:
    global _uid
    if _uid is None:
        common = xmlrpc.client.ServerProxy(ODOO_URL + "/xmlrpc/2/common")
        _uid = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PWD, {})
        if not _uid:
            raise RuntimeError("Odoo authentication failed — kiểm tra ODOO_USERNAME/PASSWORD")
    return _uid

def odoo(model: str, method: str, args: list, kwargs: dict | None = None,
         tool_name: str | None = None) -> object:
    """
    Gọi Odoo XML-RPC qua các gate bảo mật (mọi tool đều đi qua đây):
      1. sanitize model name      4. rate limit
      2. classify + deny method    5. timing + log
      3. enforce read-only (Phase 1)
    tool_name tự lấy từ hàm gọi (tool) nếu không truyền vào.
    """
    if tool_name is None:
        tool_name = sys._getframe(1).f_code.co_name   # tên tool đang gọi
    model = sanitize_model(model)

    op = classify_operation(method)
    if op is None:
        log_mcp_event("permission_denied", tool_name=tool_name, model_name=model,
                      operation=method, error_code="E403",
                      error_message=f"Method '{method}' không có trong whitelist")
        raise ValueError(f"Method '{method}' không được phép")
    if op != "read" and not write_actions_enabled():
        log_mcp_event("permission_denied", tool_name=tool_name, model_name=model,
                      operation=op, error_code="E403",
                      error_message="Write actions đang tắt (toggle Odoo "
                                    "erp_ai.write_actions_enabled)")
        raise ValueError(f"Thao tác '{op}' bị chặn — write-mode đang tắt "
                         "(erp_ai.write_actions_enabled)")

    if not check_rate_limit(tool_name or "default"):
        log_mcp_event("rate_limit", tool_name=tool_name, model_name=model, operation=op,
                      error_code="E429", error_message="Rate limit exceeded")
        raise ValueError("Quá nhiều request — thử lại sau 1 phút")

    start = time.monotonic()
    try:
        obj = xmlrpc.client.ServerProxy(ODOO_URL + "/xmlrpc/2/object")
        result = obj.execute_kw(ODOO_DB, get_uid(), ODOO_PWD, model, method, args, kwargs or {})
        log_mcp_event("model_access", tool_name=tool_name, model_name=model, operation=op,
                      duration_ms=int((time.monotonic() - start) * 1000))
        return result
    except xmlrpc.client.Fault as e:
        # Odoo commits the transaction in its service layer BEFORE serializing the
        # response, so a void (None-returning) method that already succeeded still
        # raises this marshalling Fault (allow_none=False). It can only occur
        # post-commit, so treat it as a successful void return. A method that
        # itself raised produces a different Fault (carrying its traceback), which
        # does NOT match and falls through to error + re-raise below.
        if "cannot marshal None" in str(e):
            log_mcp_event("model_access", tool_name=tool_name, model_name=model, operation=op,
                          duration_ms=int((time.monotonic() - start) * 1000))
            return None
        log_mcp_event("error", tool_name=tool_name, model_name=model, operation=op,
                      duration_ms=int((time.monotonic() - start) * 1000),
                      error_code="E500", error_message=str(e))
        raise
    except Exception as e:
        log_mcp_event("error", tool_name=tool_name, model_name=model, operation=op,
                      duration_ms=int((time.monotonic() - start) * 1000),
                      error_code="E500", error_message=str(e))
        raise

# ─── Write toggle (S3) — đọc runtime từ Odoo, cache TTL, fail-closed ──────────

_WRITE_GATE_KEY = "erp_ai.write_actions_enabled"
_WRITE_GATE_TTL_S = 5.0
_write_gate_cache = {"value": False, "expires_at": 0.0}


def write_actions_enabled() -> bool:
    """True chỉ khi ir.config_parameter[_WRITE_GATE_KEY] == "true" (strip+lower).
    Đọc qua odoo() sẵn có: search_read được classify "read" nên KHÔNG đệ quy
    qua nhánh chặn ghi. Fail-closed (spec §3): mọi lỗi đọc / key thiếu /
    value khác "true" → False; kết quả lỗi cũng cache — không spam retry."""
    now = time.monotonic()
    if now < _write_gate_cache["expires_at"]:
        return _write_gate_cache["value"]
    try:
        rows = odoo("ir.config_parameter", "search_read",
                    [[("key", "=", _WRITE_GATE_KEY)]],
                    {"fields": ["value"], "limit": 1},
                    tool_name="write_gate_check")
        # Odoo XML-RPC trả False (không phải None) cho char field rỗng → `or ""`
        value = bool(rows) and str(rows[0].get("value") or "").strip().lower() == "true"
    except Exception as e:  # noqa: BLE001 — fail-closed (spec §3)
        log_mcp_event("write_gate_error", tool_name="write_gate_check",
                      error_code="E503", error_message=str(e))
        value = False
    _write_gate_cache["value"] = value
    _write_gate_cache["expires_at"] = now + _WRITE_GATE_TTL_S
    return value

# ─── TOOLS ───────────────────────────────────────────────────────────────────

@mcp.tool()
def confirm_sale_order(order_ref: str) -> str:
    """
    Xác nhận một đơn bán hàng (sale.order) đang ở trạng thái nháp.
    draft/sent → sale. YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        order_ref: Mã đơn bán, ví dụ "S00012".
    """
    rows = odoo("sale.order", "search_read",
                [[["name", "=", order_ref]]],
                {"fields": ["id", "name", "state"], "limit": 2})
    if not rows:
        return envelope(False, f"Không tìm thấy đơn '{order_ref}'.")
    if len(rows) > 1:
        return envelope(False, f"Có nhiều đơn tên '{order_ref}'. Vui lòng nêu rõ hơn.")

    order = rows[0]
    name, state = order["name"], order["state"]
    if state in ("sale", "done"):
        return envelope(False, f"Đơn {name} đã được xác nhận rồi.")
    if state == "cancel":
        return envelope(False, f"Đơn {name} đã bị hủy, không thể xác nhận.")

    odoo("sale.order", "action_confirm", [[order["id"]]])
    return envelope(True, f"Đã xác nhận đơn {name}.",
                    ref=name, model="sale.order", res_id=order["id"], state="sale")


@mcp.tool()
def confirm_purchase_order(order_ref: str) -> str:
    """Xác nhận đơn mua hàng (purchase.order) đang ở trạng thái nháp.
    draft/sent → purchase. YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        order_ref: Mã đơn mua, ví dụ "P00003".
    """
    rows = odoo("purchase.order", "search_read",
                [[["name", "=", order_ref]]],
                {"fields": ["id", "name", "state"], "limit": 2})
    if not rows:
        return envelope(False, f"Không tìm thấy đơn mua '{order_ref}'.")
    if len(rows) > 1:
        return envelope(False,
                        f"Có nhiều đơn mua tên '{order_ref}'. Vui lòng nêu rõ hơn.")

    order = rows[0]
    name, state = order["name"], order["state"]
    if state in ("purchase", "done"):
        return envelope(False, f"Đơn mua {name} đã được xác nhận rồi.")
    if state == "cancel":
        return envelope(False, f"Đơn mua {name} đã bị hủy, không thể xác nhận.")

    odoo("purchase.order", "button_confirm", [[order["id"]]])
    return envelope(True, f"Đã xác nhận đơn mua {name}.",
                    ref=name, model="purchase.order", res_id=order["id"],
                    state="purchase")


@mcp.tool()
def post_invoice(partner_name: str = "", amount: float | None = None,
                 invoice_date: str | None = None, invoice_id: int = 0) -> str:
    """Phát hành hóa đơn nháp (account.move draft → posted) của một khách hàng.
    Áp dụng cho cả hóa đơn bán và hóa đơn mua. Hóa đơn nháp CHƯA có số (số được
    cấp khi phát hành), nên tra theo tên khách. Nếu khách có nhiều hóa đơn nháp,
    truyền thêm amount hoặc invoice_date để chọn đúng cái.
    YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        partner_name: Tên khách hàng/nhà cung cấp của hóa đơn nháp (tìm gần đúng).
        amount: Tổng tiền hóa đơn — dùng để phân biệt khi có nhiều nháp.
        invoice_date: Ngày hóa đơn (YYYY-MM-DD) — dùng để phân biệt.
        invoice_id: ID hóa đơn đã biết (ưu tiên hơn partner_name — đường nội bộ).
    """
    if invoice_id:
        rows = odoo("account.move", "search_read",
                    [[["id", "=", invoice_id],
                      ["move_type", "in", ["out_invoice", "in_invoice"]]]],
                    {"fields": ["id", "name", "state", "partner_id"], "limit": 1})
        if not rows:
            return envelope(False, f"Không tìm thấy hóa đơn ID {invoice_id}.")
        mv = rows[0]
        if mv["state"] == "posted":
            return envelope(False, f"Hóa đơn {mv['name']} đã phát hành rồi.")
        if mv["state"] != "draft":
            return envelope(False,
                            f"Hóa đơn ID {invoice_id} không ở trạng thái nháp.")
        odoo("account.move", "action_post", [[invoice_id]])
        posted = odoo("account.move", "read", [[invoice_id]],
                      {"fields": ["name", "partner_id"]})
        name = posted[0]["name"] if posted else "?"
        partner = (posted[0]["partner_id"][1]
                   if posted and posted[0].get("partner_id") else "?")
        return envelope(True, f"Đã phát hành hóa đơn {name} cho {partner}.",
                        ref=name, model="account.move", res_id=invoice_id,
                        state="posted")

    if not partner_name:
        return envelope(False,
                        "Vui lòng cho biết khách hàng (hoặc ID) của hóa đơn nháp.")

    domain = [["move_type", "in", ["out_invoice", "in_invoice"]],
              ["state", "=", "draft"],
              ["partner_id.name", "ilike", partner_name]]
    if amount is not None:
        domain.append(["amount_total", "=", amount])
    if invoice_date:
        domain.append(["invoice_date", "=", invoice_date])

    rows = odoo("account.move", "search_read", [domain],
                {"fields": ["id", "partner_id", "amount_total", "invoice_date",
                            "move_type"], "limit": 6})

    row, msg = resolve_unique(
        rows, "hóa đơn nháp",
        describe=lambda r: (f"{r['partner_id'][1] if r['partner_id'] else '?'} "
                            f"— {(r.get('amount_total') or 0):,.0f}đ "
                            f"— {r.get('invoice_date') or '—'}"),
        hint="Vui lòng nêu rõ số tiền hoặc ngày.")
    if msg:
        return envelope(False, msg)

    partner = row["partner_id"][1] if row["partner_id"] else partner_name
    odoo("account.move", "action_post", [[row["id"]]])
    posted = odoo("account.move", "read", [[row["id"]]], {"fields": ["name"]})
    name = posted[0]["name"] if posted else "?"
    return envelope(True, f"Đã phát hành hóa đơn {name} cho {partner}.",
                    ref=name, model="account.move", res_id=row["id"],
                    state="posted")


@mcp.tool()
def create_invoice_from_order(order_ref: str) -> str:
    """Tạo hóa đơn nháp (account.move) từ một đơn bán ĐÃ XÁC NHẬN.
    Chỉ tạo nháp — phát hành hóa đơn là bước riêng (post_invoice). Đơn chưa
    xác nhận sẽ bị từ chối kèm gợi ý xác nhận trước.
    YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        order_ref: Mã đơn bán, ví dụ "S00012".
    """
    try:
        rows = odoo("sale.order", "search_read",
                    [[["name", "=", order_ref]]],
                    {"fields": ["id", "name", "state", "invoice_status",
                                "invoice_ids"], "limit": 2})
        if not rows:
            return envelope(False, f"Không tìm thấy đơn '{order_ref}'.")
        if len(rows) > 1:
            return envelope(False, f"Có nhiều đơn tên '{order_ref}'. Vui lòng nêu rõ hơn.")

        so = rows[0]
        name = so["name"]
        if so["state"] not in ("sale", "done"):
            return envelope(False, f"Đơn {name} chưa xác nhận (trạng thái nháp). "
                                   f"Hãy xác nhận đơn trước khi tạo hóa đơn.")
        if so["invoice_status"] != "to invoice":
            # Verified-live: after full invoicing Odoo 19 reports 'no' (not
            # 'invoiced'), so one guard covers both not-deliverable and done.
            return envelope(False, f"Không có gì để xuất hóa đơn cho đơn {name} "
                                   f"(chưa giao hàng, hoặc đã xuất đủ).")

        before = set(so["invoice_ids"] or [])
        ctx = {"active_model": "sale.order", "active_ids": [so["id"]],
               "active_id": so["id"]}
        wid = odoo("sale.advance.payment.inv", "create",
                   [{"advance_payment_method": "delivered"}], {"context": ctx})
        # create_invoices returns an action dict Odoo can't marshal over
        # XML-RPC; odoo() maps that benign Fault to None — success is verified
        # by re-reading invoice_ids below, never from this return value.
        odoo("sale.advance.payment.inv", "create_invoices", [[wid]],
             {"context": ctx})

        after = odoo("sale.order", "read", [[so["id"]]], {"fields": ["invoice_ids"]})
        new_ids = [i for i in (after[0]["invoice_ids"] if after else [])
                   if i not in before]
        if not new_ids:
            return envelope(False, f"Không tạo được hóa đơn cho đơn {name} — "
                                   f"vui lòng kiểm tra trên Odoo.")
        return envelope(True, f"Đã tạo hóa đơn nháp cho đơn {name} (chưa phát hành).",
                        ref=None, model="account.move", res_id=max(new_ids),
                        state="draft")
    except Exception as e:  # noqa: BLE001 — never raise through the MCP tool
        return envelope(False, f"Lỗi khi tạo hóa đơn cho đơn {order_ref}: {e}")


@mcp.tool()
def validate_picking(picking_ref: str) -> str:
    """Xác nhận phiếu giao/nhận hàng (stock.picking) đã được reserve đủ.
    Chỉ hoạt động khi state = 'assigned' — ở trạng thái này Odoo 19 đã tự set
    số lượng thực = số lượng reserve trên mọi dòng, nên button_validate chạy
    thẳng (không pop wizard). Nếu vẫn trả về dict (vd backorder một phần) thì
    báo an toàn để xử lý trực tiếp trên Odoo.
    YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        picking_ref: Mã phiếu, ví dụ "WH/OUT/00001" hoặc "WH/IN/00005".
    """
    rows = odoo("stock.picking", "search_read",
                [[["name", "=", picking_ref]]],
                {"fields": ["id", "name", "state"], "limit": 2})
    if not rows:
        return f"Không tìm thấy phiếu '{picking_ref}'."
    if len(rows) > 1:
        return f"Có nhiều phiếu tên '{picking_ref}'. Vui lòng nêu rõ hơn."

    pick = rows[0]
    name, state = pick["name"], pick["state"]
    if state == "done":
        return f"Phiếu {name} đã được xác nhận rồi."
    if state == "cancel":
        return f"Phiếu {name} đã bị hủy."
    if state != "assigned":
        return (f"Phiếu {name} chưa sẵn sàng (trạng thái: {state}). "
                f"Cần reserve đủ hàng trước khi xác nhận.")

    # Odoo 19: an 'assigned' picking already has done-qty = reserved on every
    # move, so button_validate completes directly (no immediate-transfer wizard).
    result = odoo("stock.picking", "button_validate", [[pick["id"]]])
    if isinstance(result, dict):
        return (f"Phiếu {name} cần thao tác bổ sung trên Odoo "
                f"(wizard không hỗ trợ qua API). Vui lòng xử lý trực tiếp.")
    return f"Đã xác nhận phiếu {name}."


def _validate_order_pickings(picking_ids, type_code):
    """Validate mọi phiếu `assigned` (đã sẵn sàng) của một đơn, lọc theo
    picking_type_code ("outgoing" = giao, "incoming" = nhận). Trả (status, val):
      ("none", None)            — không còn phiếu chờ → caller pass-through
      ("not_ready", states_str) — có phiếu chờ nhưng chưa phiếu nào assigned
      ("wizard", picking_name)  — button_validate trả dict → DỪNG ngay
      ("done", k)               — đã validate k phiếu
    Wording nằm ở call-site — helper không dựng thông điệp người dùng."""
    pickings = []
    if picking_ids:
        pickings = odoo("stock.picking", "search_read",
                        [[["id", "in", picking_ids],
                          ["picking_type_code", "=", type_code]]],
                        {"fields": ["id", "name", "state"]})
    pending = [p for p in pickings if p["state"] not in ("done", "cancel")]
    if not pending:
        return "none", None
    assigned = [p for p in pending if p["state"] == "assigned"]
    if not assigned:
        return "not_ready", ", ".join(sorted({p["state"] for p in pending}))
    for p in assigned:
        # Odoo 19: phiếu 'assigned' đã auto-set done-qty nên button_validate
        # chạy thẳng; dict trả về = wizard → dừng an toàn.
        result = odoo("stock.picking", "button_validate", [[p["id"]]])
        if isinstance(result, dict):
            return "wizard", p["name"]
    return "done", len(assigned)


@mcp.tool()
def deliver_order(order_ref: str) -> str:
    """Giao hàng cho một đơn bán ĐÃ XÁC NHẬN: xác nhận mọi phiếu xuất kho
    (stock.picking) đã reserve đủ của đơn. Đơn không có phiếu cần giao
    (dịch vụ / đã giao đủ) được coi là hoàn tất — chuỗi đi tiếp bước
    tạo hóa đơn. YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        order_ref: Mã đơn bán, ví dụ "S00012".
    """
    try:
        rows = odoo("sale.order", "search_read",
                    [[["name", "=", order_ref]]],
                    {"fields": ["id", "name", "state", "picking_ids"],
                     "limit": 2})
        if not rows:
            return envelope(False, f"Không tìm thấy đơn '{order_ref}'.")
        if len(rows) > 1:
            return envelope(False,
                            f"Có nhiều đơn tên '{order_ref}'. Vui lòng nêu rõ hơn.")

        so = rows[0]
        name = so["name"]
        if so["state"] not in ("sale", "done"):
            return envelope(False, f"Đơn {name} chưa xác nhận (trạng thái nháp). "
                                   f"Hãy xác nhận đơn trước khi giao hàng.")

        status, val = _validate_order_pickings(so["picking_ids"], "outgoing")
        if status == "none":
            # Pass-through: dịch vụ / giao ngay / đã giao đủ — chuỗi vẫn mời
            # bước "Tạo hóa đơn" tiếp theo.
            return envelope(True, f"Đơn {name} không có phiếu cần giao "
                                  f"(dịch vụ hoặc đã giao đủ).",
                            ref=name, model="sale.order", res_id=so["id"],
                            state="sale")
        if status == "not_ready":
            return envelope(False,
                            f"Phiếu giao của đơn {name} chưa reserve đủ hàng "
                            f"(trạng thái: {val}). Kiểm tra tồn kho trước khi giao.")
        if status == "wizard":
            return envelope(False,
                            f"Phiếu {val} cần thao tác bổ sung trên Odoo "
                            f"(wizard không hỗ trợ qua API). Vui lòng xử lý trực tiếp.")
        return envelope(True, f"Đã giao hàng cho đơn {name} ({val} phiếu).",
                        ref=name, model="sale.order", res_id=so["id"], state="sale")
    except Exception as e:  # noqa: BLE001 — không exception nào xuyên qua MCP tool
        return envelope(False, f"Lỗi khi giao hàng cho đơn {order_ref}: {e}")


@mcp.tool()
def receive_order(order_ref: str) -> str:
    """Nhận hàng cho một đơn mua ĐÃ XÁC NHẬN: xác nhận mọi phiếu nhập kho
    (stock.picking) đã sẵn sàng của đơn. Đơn không có phiếu cần nhận
    (dịch vụ / đã nhận đủ) được coi là hoàn tất — chuỗi đi tiếp bước
    lập hóa đơn NCC. YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        order_ref: Mã đơn mua, ví dụ "P00003".
    """
    try:
        rows = odoo("purchase.order", "search_read",
                    [[["name", "=", order_ref]]],
                    {"fields": ["id", "name", "state", "picking_ids"],
                     "limit": 2})
        if not rows:
            return envelope(False, f"Không tìm thấy đơn mua '{order_ref}'.")
        if len(rows) > 1:
            return envelope(False,
                            f"Có nhiều đơn mua tên '{order_ref}'. Vui lòng nêu rõ hơn.")

        po = rows[0]
        name = po["name"]
        if po["state"] not in ("purchase", "done"):
            return envelope(False, f"Đơn mua {name} chưa xác nhận. "
                                   f"Hãy xác nhận đơn trước khi nhận hàng.")

        status, val = _validate_order_pickings(po["picking_ids"], "incoming")
        if status == "none":
            return envelope(True, f"Đơn mua {name} không có phiếu cần nhận "
                                  f"(dịch vụ hoặc đã nhận đủ).",
                            ref=name, model="purchase.order", res_id=po["id"],
                            state="purchase")
        if status == "not_ready":
            return envelope(False,
                            f"Phiếu nhập của đơn mua {name} chưa sẵn sàng nhận "
                            f"(trạng thái: {val}).")
        if status == "wizard":
            return envelope(False,
                            f"Phiếu {val} cần thao tác bổ sung trên Odoo "
                            f"(wizard không hỗ trợ qua API). Vui lòng xử lý trực tiếp.")
        return envelope(True, f"Đã nhận hàng cho đơn mua {name} ({val} phiếu).",
                        ref=name, model="purchase.order", res_id=po["id"],
                        state="purchase")
    except Exception as e:  # noqa: BLE001 — không exception nào xuyên qua MCP tool
        return envelope(False, f"Lỗi khi nhận hàng cho đơn mua {order_ref}: {e}")


@mcp.tool()
def create_bill_from_po(order_ref: str) -> str:
    """Tạo hóa đơn nhà cung cấp (account.move nháp) từ một đơn mua ĐÃ XÁC NHẬN
    và ĐÃ NHẬN HÀNG. Chỉ tạo nháp — phát hành là bước riêng (post_invoice).
    Bill Date được đặt = hôm nay (Odoo bắt buộc trước khi phát hành).
    YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        order_ref: Mã đơn mua, ví dụ "P00003".
    """
    try:
        rows = odoo("purchase.order", "search_read",
                    [[["name", "=", order_ref]]],
                    {"fields": ["id", "name", "state", "invoice_status",
                                "invoice_ids"], "limit": 2})
        if not rows:
            return envelope(False, f"Không tìm thấy đơn mua '{order_ref}'.")
        if len(rows) > 1:
            return envelope(False,
                            f"Có nhiều đơn mua tên '{order_ref}'. Vui lòng nêu rõ hơn.")

        po = rows[0]
        name = po["name"]
        if po["state"] not in ("purchase", "done"):
            return envelope(False, f"Đơn mua {name} chưa xác nhận. "
                                   f"Hãy xác nhận đơn trước khi lập hóa đơn.")
        if po["invoice_status"] != "to invoice":
            return envelope(False,
                            f"Chưa có gì để lập hóa đơn NCC cho đơn mua {name} "
                            f"(chưa nhận hàng, hoặc đã lập đủ).")

        before = set(po["invoice_ids"] or [])
        # action_create_invoice trả action dict — không tin return value; verify
        # bằng đọc lại invoice_ids (verified-live 2026-07-03 trên P00015).
        odoo("purchase.order", "action_create_invoice", [[po["id"]]])
        after = odoo("purchase.order", "read", [[po["id"]]],
                     {"fields": ["invoice_ids"]})
        new_ids = [i for i in (after[0]["invoice_ids"] if after else [])
                   if i not in before]
        if not new_ids:
            return envelope(False, f"Không tạo được hóa đơn cho đơn mua {name} — "
                                   f"vui lòng kiểm tra trên Odoo.")
        # Bill Date bắt buộc trước khi post (verified-live: "The Bill/Refund
        # date is required to validate this document.")
        odoo("account.move", "write", [new_ids, {"invoice_date": today_iso()}])
        return envelope(True, f"Đã tạo hóa đơn NCC (nháp) cho đơn mua {name}.",
                        ref=None, model="account.move", res_id=max(new_ids),
                        state="draft")
    except Exception as e:  # noqa: BLE001 — không exception nào xuyên qua MCP tool
        return envelope(False, f"Lỗi khi tạo hóa đơn cho đơn mua {order_ref}: {e}")


def _resolve_partner(name, kind_label, hint):
    """Resolve a partner name → unique row via the disambiguation pattern.
    Lenient (no rank filter); returns (row, None) or (None, listing/not-found msg)."""
    rows = odoo("res.partner", "search_read",
                [[["name", "ilike", name]]],
                {"fields": ["id", "name", "email"], "limit": 6})
    return resolve_unique(
        rows, kind_label,
        describe=lambda r: f"{r['name']} — {r.get('email') or '—'}",
        hint=hint)


def _resolve_product(term, ok_field):
    """Resolve a product name/code → unique row. `ok_field` ANDs a flag clause:
    'sale_ok' (SO), 'purchase_ok' (PO), or 'is_storable' (inventory)."""
    rows = odoo("product.product", "search_read",
                [["|", ["name", "ilike", term],
                      ["default_code", "ilike", term],
                  [ok_field, "=", True]]],
                {"fields": ["id", "name", "default_code", "list_price"],
                 "limit": 6})
    return resolve_unique(
        rows, f"sản phẩm '{term}'",
        describe=lambda r: (f"{r['name']} [{r.get('default_code') or '-'}] "
                            f"— {r['list_price']:,.0f}đ"),
        hint="Vui lòng nêu rõ tên sản phẩm.")


@mcp.tool()
def create_quotation(partner_name: str = "", lines: list | None = None,
                     partner_id: int = 0) -> str:
    """Tạo báo giá nháp (sale.order) cho một khách hàng với các dòng sản phẩm.
    Ưu tiên ID đã resolve (partner_id, mỗi dòng product_id); nếu vắng ID thì
    resolve theo tên (partner_name, mỗi dòng product). Nếu có gì không rõ thì
    DỪNG, không tạo đơn dở. YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        partner_name: Tên khách hàng (tìm gần đúng) — dùng khi không có partner_id.
        lines: Danh sách dòng hàng, mỗi dòng {"product": "<tên>", "qty": <số>} hoặc
               {"product_id": <id>, "qty": <số>}, có thể kèm "price_unit": <số>
               (giá đã xác nhận với người dùng — nếu vắng, Odoo tự tính theo
               bảng giá của khách, có thể LỆCH với giá đã hỏi xác nhận).
        partner_id: ID khách hàng đã resolve (ưu tiên hơn partner_name).
    """
    lines = lines or []
    if not lines:
        return envelope(False, "Vui lòng cho biết sản phẩm và số lượng cần báo giá.")

    if partner_id:
        prows = odoo("res.partner", "read", [[partner_id]], {"fields": ["id", "name"]})
        if not prows:
            return envelope(False, f"Không tìm thấy khách hàng ID {partner_id}.")
        partner = prows[0]
    else:
        partner, msg = _resolve_partner(partner_name, "khách hàng",
                                        "Vui lòng nêu rõ tên khách hàng.")
        if msg:
            return envelope(False, msg)

    order_line = []
    for line in lines:
        pid = line.get("product_id")
        price_unit = line.get("price_unit")
        if pid:
            vals = {"product_id": pid, "product_uom_qty": line["qty"]}
            if price_unit is not None:
                vals["price_unit"] = price_unit
            order_line.append((0, 0, vals))
            continue
        prod, pmsg = _resolve_product(line["product"], "sale_ok")
        if pmsg:
            return envelope(False, pmsg)
        vals = {"product_id": prod["id"], "product_uom_qty": line["qty"]}
        if price_unit is not None:
            vals["price_unit"] = price_unit
        order_line.append((0, 0, vals))

    sid = odoo("sale.order", "create",
               [{"partner_id": partner["id"], "order_line": order_line}])
    so = odoo("sale.order", "read", [[sid]], {"fields": ["name"]})
    name = so[0]["name"] if so else "?"
    return envelope(True,
                    f"Đã tạo báo giá {name} (nháp) cho {partner['name']} ({len(lines)} dòng).",
                    ref=name, model="sale.order", res_id=sid, state="draft")


@mcp.tool()
def create_rfq(supplier_name: str = "", lines: list | None = None,
               partner_id: int = 0) -> str:
    """Tạo RFQ — đơn mua nháp (purchase.order) cho một nhà cung cấp với các dòng
    sản phẩm. Ưu tiên ID đã resolve (partner_id, mỗi dòng product_id); nếu vắng ID
    thì resolve theo tên. Nếu có gì không rõ thì DỪNG, không tạo đơn dở. YÊU CẦU
    XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        supplier_name: Tên nhà cung cấp (tìm gần đúng) — dùng khi không có partner_id.
        lines: Danh sách dòng hàng, mỗi dòng {"product": "<tên>", "qty": <số>} hoặc
               {"product_id": <id>, "qty": <số>}.
        partner_id: ID nhà cung cấp đã resolve (ưu tiên hơn supplier_name).
    """
    lines = lines or []
    if not lines:
        return envelope(False, "Vui lòng cho biết sản phẩm và số lượng cần đặt mua.")

    if partner_id:
        vrows = odoo("res.partner", "read", [[partner_id]], {"fields": ["id", "name"]})
        if not vrows:
            return envelope(False, f"Không tìm thấy nhà cung cấp ID {partner_id}.")
        vendor = vrows[0]
    else:
        vendor, msg = _resolve_partner(supplier_name, "nhà cung cấp",
                                       "Vui lòng nêu rõ tên nhà cung cấp.")
        if msg:
            return envelope(False, msg)

    order_line = []
    for line in lines:
        pid = line.get("product_id")
        if pid:
            order_line.append((0, 0, {"product_id": pid,
                                      "product_qty": line["qty"]}))
            continue
        prod, pmsg = _resolve_product(line["product"], "purchase_ok")
        if pmsg:
            return envelope(False, pmsg)
        order_line.append((0, 0, {"product_id": prod["id"],
                                  "product_qty": line["qty"]}))

    pid_ = odoo("purchase.order", "create",
                [{"partner_id": vendor["id"], "order_line": order_line}])
    po = odoo("purchase.order", "read", [[pid_]], {"fields": ["name"]})
    name = po[0]["name"] if po else "?"
    return envelope(True,
                    f"Đã tạo RFQ {name} (nháp) cho {vendor['name']} ({len(lines)} dòng).",
                    ref=name, model="purchase.order", res_id=pid_, state="draft")


_EDITABLE_STATES = ("draft", "sent")


def _apply_line_ops(model: str, qty_field: str, order_ref: str, ops: list) -> str:
    """Validate + apply o2m commands to a DRAFT order's lines. Shared body for
    update_quotation_lines / update_rfq_lines — each passes its own model +
    qty_field (Invariant #1). State-gate here is the real gate (Invariant #4)."""
    rows = odoo(model, "search_read", [[["name", "=", order_ref]]],
                {"fields": ["id", "name", "state"], "limit": 2})
    if not rows:
        return envelope(False, f"Không tìm thấy đơn '{order_ref}'.")
    if len(rows) > 1:
        return envelope(False, f"Có nhiều đơn tên '{order_ref}'. Vui lòng nêu rõ hơn.")
    order = rows[0]
    name, state = order["name"], order["state"]
    if state not in _EDITABLE_STATES:
        return envelope(False, f"Đơn {name} đã xác nhận, không thể sửa.")
    if not ops:
        return envelope(False, "Không có thay đổi nào để áp dụng.")

    cmds = []
    for op in ops:
        kind = op.get("op")
        if kind == "add":
            pid, qty = op.get("product_id"), op.get("qty")
            if not isinstance(pid, int) or not isinstance(qty, (int, float)) or qty <= 0:
                return envelope(False, "Lệnh thêm dòng không hợp lệ.")
            cmds.append((0, 0, {"product_id": pid, qty_field: qty}))
        elif kind == "remove":
            lid = op.get("line_id")
            if not isinstance(lid, int):
                return envelope(False, "Lệnh xóa dòng không hợp lệ.")
            cmds.append((2, lid, 0))
        elif kind == "set_qty":
            lid, qty = op.get("line_id"), op.get("qty")
            if not isinstance(lid, int) or not isinstance(qty, (int, float)) or qty <= 0:
                return envelope(False, "Lệnh đổi số lượng không hợp lệ.")
            cmds.append((1, lid, {qty_field: qty}))
        else:
            return envelope(False, f"Thao tác không hỗ trợ: {kind!r}.")

    odoo(model, "write", [[order["id"]], {"order_line": cmds}])
    label = "báo giá" if model == "sale.order" else "đơn mua"
    return envelope(True, f"Đã sửa {label} {name} ({len(cmds)} thay đổi).",
                    ref=name, model=model, res_id=order["id"], state=state)


@mcp.tool()
def update_quotation_lines(order_ref: str, ops: list | None = None) -> str:
    """Sửa dòng hàng của BÁO GIÁ (sale.order). Chỉ áp dụng được cho đơn nháp
    (draft/sent); nếu đơn đã xác nhận, tool trả về lỗi và tầng điều phối sẽ đề nghị
    ghi chú nội bộ. ops đã resolve theo ID; coordinator dựng ops, KHÔNG để LLM tự dựng.
    YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        order_ref: Mã đơn bán, ví dụ "S00012".
        ops: [{"op":"add","product_id":int,"qty":float} |
              {"op":"remove","line_id":int} |
              {"op":"set_qty","line_id":int,"qty":float}]
    """
    try:
        return _apply_line_ops("sale.order", "product_uom_qty", order_ref, ops or [])
    except Exception as e:  # noqa: BLE001
        return envelope(False, f"Lỗi khi sửa báo giá {order_ref}: {e}")


@mcp.tool()
def update_rfq_lines(order_ref: str, ops: list | None = None) -> str:
    """Sửa dòng hàng của ĐƠN MUA (purchase.order). Chỉ áp dụng được cho đơn nháp
    (draft/sent); nếu đơn đã xác nhận, tool trả về lỗi và tầng điều phối sẽ đề nghị
    ghi chú nội bộ. ops đã resolve theo ID. YÊU CẦU XÁC NHẬN trước khi gọi.

    Args:
        order_ref: Mã đơn mua, ví dụ "P00003".
        ops: cùng schema với update_quotation_lines.
    """
    try:
        return _apply_line_ops("purchase.order", "product_qty", order_ref, ops or [])
    except Exception as e:  # noqa: BLE001
        return envelope(False, f"Lỗi khi sửa đơn mua {order_ref}: {e}")


_FLAGGABLE_MODELS = ("sale.order", "purchase.order")


@mcp.tool()
def flag_order_for_review(model: str, order_ref: str, note: str) -> str:
    """Ghi một ghi chú nội bộ (message_post) lên chatter của đơn để báo quản lý —
    dùng khi đơn ĐÃ xác nhận không sửa trực tiếp được. Chỉ áp dụng cho sale.order /
    purchase.order (Invariant #6).

    Args:
        model: "sale.order" | "purchase.order".
        order_ref: Mã đơn, ví dụ "S00012" / "P00003".
        note: Nội dung ghi chú (tiếng Việt).
    """
    try:
        if model not in _FLAGGABLE_MODELS:
            return envelope(False, "Model không được hỗ trợ.")
        rows = odoo(model, "search_read", [[["name", "=", order_ref]]],
                    {"fields": ["id", "name", "state"], "limit": 2})
        if not rows:
            return envelope(False, f"Không tìm thấy đơn '{order_ref}'.")
        if len(rows) > 1:
            return envelope(False, f"Có nhiều đơn tên '{order_ref}'. Vui lòng nêu rõ hơn.")
        order = rows[0]
        # message_post may return a recordset that XML-RPC can't marshal (gateway
        # then returns None post-commit). We don't use the return value.
        odoo(model, "message_post", [[order["id"]]], {"body": note})
        return envelope(True,
                        f"Đã ghi chú nội bộ trên đơn {order['name']} để báo quản lý.",
                        ref=order["name"], model=model, res_id=order["id"],
                        state=order["state"])
    except Exception as e:  # noqa: BLE001
        return envelope(False, f"Lỗi khi ghi chú đơn {order_ref}: {e}")


@mcp.tool()
def inventory_adjustment(new_qty: float, product_name: str = "",
                         location_name: str | None = None, product_id: int = 0) -> str:
    """Điều chỉnh tồn kho thực tế của một sản phẩm về một SỐ TUYỆT ĐỐI tại một
    vị trí kho (kiểm kê). new_qty là tồn kho KẾT QUẢ mong muốn, không phải lượng
    tăng/giảm. Nếu không nêu vị trí thì dùng kho chính. YÊU CẦU XÁC NHẬN từ người
    dùng trước khi gọi.

    Args:
        product_name: Tên sản phẩm lưu kho (tìm gần đúng).
        new_qty: Tồn kho kết quả mong muốn (>= 0).
        location_name: Tên vị trí kho (tùy chọn; bỏ trống = kho chính).
    """
    if new_qty < 0:
        return "Số lượng tồn kho không hợp lệ (không âm)."

    if product_id:
        prows = odoo("product.product", "read", [[product_id]], {"fields": ["id", "name"]})
        if not prows:
            return f"Không tìm thấy sản phẩm ID {product_id}."
        prod = prows[0]
    else:
        prod, msg = _resolve_product(product_name, "is_storable")
        if msg:
            return msg

    if location_name:
        lrows = odoo("stock.location", "search_read",
                     [[["usage", "=", "internal"],
                       ["complete_name", "ilike", location_name]]],
                     {"fields": ["id", "complete_name"], "limit": 6})
        loc, lmsg = resolve_unique(
            lrows, "vị trí kho",
            describe=lambda r: r["complete_name"],
            hint="Vui lòng nêu rõ tên vị trí kho.")
        if lmsg:
            return lmsg
    else:
        wh = odoo("stock.warehouse", "search_read", [[]],
                  {"fields": ["lot_stock_id"], "limit": 1})
        if not wh:
            return "Không tìm thấy kho mặc định."
        loc = {"id": wh[0]["lot_stock_id"][0],
               "complete_name": wh[0]["lot_stock_id"][1]}

    quants = odoo("stock.quant", "search_read",
                  [[["product_id", "=", prod["id"]],
                    ["location_id", "=", loc["id"]]]],
                  {"fields": ["id", "quantity"], "limit": 1})
    if quants:
        qid = quants[0]["id"]
        old = quants[0]["quantity"]
        odoo("stock.quant", "write", [[qid], {"inventory_quantity": new_qty}])
    else:
        old = 0.0
        qid = odoo("stock.quant", "create",
                   [{"product_id": prod["id"], "location_id": loc["id"],
                     "inventory_quantity": new_qty}])

    res = odoo("stock.quant", "action_apply_inventory", [[qid]])
    if isinstance(res, dict):
        return (f"Tồn kho {prod['name']} cần xử lý xung đột kiểm kê trên Odoo "
                f"(sản phẩm theo lô/sê-ri). Vui lòng xử lý trực tiếp.")

    q = odoo("stock.quant", "read", [[qid]], {"fields": ["quantity"]})
    now = q[0]["quantity"] if q else new_qty
    return (f"Đã điều chỉnh tồn kho {prod['name']} tại {loc['complete_name']}: "
            f"{old:g} → {now:g}.")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="sse")
