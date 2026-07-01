"""
Odoo MCP Server — Phase 2: Write/Do-tools only
Reads moved to backend/src/erp_query/ (Tasks 1–8).

Transport: HTTP/SSE tại port 8001
Connect:   http://mcp-odoo:8001/sse  (từ backend container)
"""
import os
import re
import sys
import time
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import xmlrpc.client
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("odoo-mcp", host="0.0.0.0", port=8001)

# ─── Config ───────────────────────────────────────────────────────────────────

ODOO_URL  = os.environ["ODOO_URL"]       # http://host.docker.internal:8069
ODOO_DB   = os.environ["ODOO_DB"]        # odoo
ODOO_USER = os.environ["ODOO_USERNAME"]  # phamhao14170@gmail.com
ODOO_PWD  = os.environ["ODOO_PASSWORD"]

WRITE_ENABLED = os.environ.get("WRITE_ACTIONS_ENABLED", "false").lower() == "true"
RATE_LIMIT    = int(os.environ.get("MCP_RATE_LIMIT", "60"))   # calls/phút
DATABASE_URL  = os.environ.get("DATABASE_URL")               # log nếu có; không thì skip

# ─── Security — port từ mcp_server addon (controllers/utils.py) ───────────────

# Ánh xạ XML-RPC method → loại operation. Method không có trong map = bị từ chối
# (deny-by-default). Phase 3 dùng "create|write|unlink" để biết cần confirmation gate.
ODOO_METHOD_OPERATION_MAP = {
    # READ — an toàn
    "read": "read", "search": "read", "search_read": "read",
    "search_count": "read", "name_search": "read", "fields_get": "read",
    "read_group": "read", "formatted_read_group": "read",
    "default_get": "read", "name_get": "read", "get_metadata": "read",
    # CREATE — Phase 3: cần confirmation
    "create": "create", "copy": "create", "name_create": "create",
    # WRITE — Phase 3: cần confirmation
    "write": "write", "toggle_active": "write",
    "action_archive": "write", "message_post": "write",
    "action_confirm": "write",
    "button_confirm": "write",
    "action_post": "write",
    "button_validate": "write",
    "action_apply_inventory": "write",
    # UNLINK — Phase 3: cần confirmation + cảnh báo
    "unlink": "unlink", "action_delete": "unlink",
}

def classify_operation(method: str) -> str | None:
    """None = method không được phép (deny-by-default)."""
    return ODOO_METHOD_OPERATION_MAP.get(str(method).lower().strip())

def sanitize_model(name: str) -> str:
    """Chặn injection qua tên model — chỉ cho [a-zA-Z0-9._]."""
    if not name or not re.match(r"^[a-zA-Z0-9._]+$", name):
        raise ValueError(f"Tên model không hợp lệ: {name!r}")
    return name.strip()

# ─── Rate limiting — sliding window in-memory (port từ rate_limiting.py) ───────

_rate_cache: dict[str, list[datetime]] = defaultdict(list)
_rate_lock = threading.Lock()

def check_rate_limit(caller: str = "default") -> bool:
    """True = còn trong giới hạn; False = vượt limit."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=1)
    with _rate_lock:
        _rate_cache[caller] = [t for t in _rate_cache[caller] if t > cutoff]
        if len(_rate_cache[caller]) >= RATE_LIMIT:
            return False
        _rate_cache[caller].append(now)
        return True

# ─── Logging vào PostgreSQL (port pattern log_event từ mcp_log.py) ─────────────

MAX_TEXT = 10_000
_db_conn = None
_db_lock = threading.Lock()

def _truncate(text: str | None) -> str | None:
    if text and len(text) > MAX_TEXT:
        return text[:MAX_TEXT] + "... [truncated]"
    return text

def _get_db():
    """Lazy connection, reconnect khi lỗi. None nếu không cấu hình DATABASE_URL."""
    global _db_conn
    if not DATABASE_URL:
        return None
    if _db_conn is None or getattr(_db_conn, "closed", 1):
        import psycopg2
        _db_conn = psycopg2.connect(DATABASE_URL)
        _db_conn.autocommit = True
    return _db_conn

def log_mcp_event(event_type: str, *, tool_name=None, model_name=None,
                  operation=None, duration_ms=None, error_code=None,
                  error_message=None, caller="mcp-odoo") -> None:
    """Ghi mcp_call_log. Mọi lỗi log đều nuốt — KHÔNG được làm hỏng tool."""
    global _db_conn
    try:
        with _db_lock:
            conn = _get_db()
            if conn is None:
                return
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO mcp_call_log
                    (event_type, caller, tool_name, model_name, operation,
                     duration_ms, error_code, error_message)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """, (event_type, caller, tool_name, model_name, operation,
                      duration_ms, error_code, _truncate(error_message)))
    except Exception:
        _db_conn = None   # ép reconnect lần sau

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
    if op != "read" and not WRITE_ENABLED:
        log_mcp_event("permission_denied", tool_name=tool_name, model_name=model,
                      operation=op, error_code="E403",
                      error_message="Write actions chưa bật (WRITE_ACTIONS_ENABLED=false)")
        raise ValueError(f"Thao tác '{op}' bị chặn — WRITE_ACTIONS_ENABLED=false")

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

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def today_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def resolve_unique(rows, kind_label, describe, hint=""):
    """Pick a single record from candidate rows, or describe the choices.

    Returns (row, None) when exactly one candidate matches; (None, message)
    when none match or several do. `describe(row)` returns a short
    distinguishing string used in the multi-candidate listing. Reusable by
    any resolution tool.
    """
    if not rows:
        return None, f"Không tìm thấy {kind_label} nào phù hợp."
    if len(rows) == 1:
        return rows[0], None
    listing = "\n".join(f"  • {describe(r)}" for r in rows)
    msg = f"Có nhiều {kind_label}:\n{listing}"
    if hint:
        msg += f"\n{hint}"
    return None, msg

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
        return f"Không tìm thấy đơn '{order_ref}'."
    if len(rows) > 1:
        return f"Có nhiều đơn tên '{order_ref}'. Vui lòng nêu rõ hơn."

    order = rows[0]
    name, state = order["name"], order["state"]
    if state in ("sale", "done"):
        return f"Đơn {name} đã được xác nhận rồi."
    if state == "cancel":
        return f"Đơn {name} đã bị hủy, không thể xác nhận."

    odoo("sale.order", "action_confirm", [[order["id"]]])
    return f"Đã xác nhận đơn {name}."


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
        return f"Không tìm thấy đơn mua '{order_ref}'."
    if len(rows) > 1:
        return f"Có nhiều đơn mua tên '{order_ref}'. Vui lòng nêu rõ hơn."

    order = rows[0]
    name, state = order["name"], order["state"]
    if state in ("purchase", "done"):
        return f"Đơn mua {name} đã được xác nhận rồi."
    if state == "cancel":
        return f"Đơn mua {name} đã bị hủy, không thể xác nhận."

    odoo("purchase.order", "button_confirm", [[order["id"]]])
    return f"Đã xác nhận đơn mua {name}."


@mcp.tool()
def post_invoice(partner_name: str, amount: float | None = None,
                 invoice_date: str | None = None) -> str:
    """Phát hành hóa đơn nháp (account.move draft → posted) của một khách hàng.
    Áp dụng cho cả hóa đơn bán và hóa đơn mua. Hóa đơn nháp CHƯA có số (số được
    cấp khi phát hành), nên tra theo tên khách. Nếu khách có nhiều hóa đơn nháp,
    truyền thêm amount hoặc invoice_date để chọn đúng cái.
    YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        partner_name: Tên khách hàng/nhà cung cấp của hóa đơn nháp (tìm gần đúng).
        amount: Tổng tiền hóa đơn — dùng để phân biệt khi có nhiều nháp.
        invoice_date: Ngày hóa đơn (YYYY-MM-DD) — dùng để phân biệt.
    """
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
        return msg

    partner = row["partner_id"][1] if row["partner_id"] else partner_name
    odoo("account.move", "action_post", [[row["id"]]])
    posted = odoo("account.move", "read", [[row["id"]]], {"fields": ["name"]})
    name = posted[0]["name"] if posted else "?"
    return f"Đã phát hành hóa đơn {name} cho {partner}."


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
               {"product_id": <id>, "qty": <số>}.
        partner_id: ID khách hàng đã resolve (ưu tiên hơn partner_name).
    """
    lines = lines or []
    if not lines:
        return "Vui lòng cho biết sản phẩm và số lượng cần báo giá."

    if partner_id:
        prows = odoo("res.partner", "read", [[partner_id]], {"fields": ["id", "name"]})
        if not prows:
            return f"Không tìm thấy khách hàng ID {partner_id}."
        partner = prows[0]
    else:
        partner, msg = _resolve_partner(partner_name, "khách hàng",
                                        "Vui lòng nêu rõ tên khách hàng.")
        if msg:
            return msg

    order_line = []
    for line in lines:
        pid = line.get("product_id")
        if pid:
            order_line.append((0, 0, {"product_id": pid,
                                      "product_uom_qty": line["qty"]}))
            continue
        prod, pmsg = _resolve_product(line["product"], "sale_ok")
        if pmsg:
            return pmsg
        order_line.append((0, 0, {"product_id": prod["id"],
                                  "product_uom_qty": line["qty"]}))

    sid = odoo("sale.order", "create",
               [{"partner_id": partner["id"], "order_line": order_line}])
    so = odoo("sale.order", "read", [[sid]], {"fields": ["name"]})
    name = so[0]["name"] if so else "?"
    return f"Đã tạo báo giá {name} cho {partner['name']} ({len(lines)} dòng)."


@mcp.tool()
def create_rfq(supplier_name: str, lines: list) -> str:
    """Tạo RFQ — đơn mua nháp (purchase.order) cho một nhà cung cấp với các dòng
    sản phẩm. Resolve tên NCC + tên từng sản phẩm; nếu có gì không rõ thì DỪNG,
    không tạo đơn dở. YÊU CẦU XÁC NHẬN từ người dùng trước khi gọi.

    Args:
        supplier_name: Tên nhà cung cấp (tìm gần đúng).
        lines: Danh sách dòng hàng, mỗi dòng {"product": "<tên SP>", "qty": <số>}.
    """
    if not lines:
        return "Vui lòng cho biết sản phẩm và số lượng cần đặt mua."

    vendor, msg = _resolve_partner(supplier_name, "nhà cung cấp",
                                   "Vui lòng nêu rõ tên nhà cung cấp.")
    if msg:
        return msg

    order_line = []
    for line in lines:
        prod, pmsg = _resolve_product(line["product"], "purchase_ok")
        if pmsg:
            return pmsg
        order_line.append((0, 0, {"product_id": prod["id"],
                                  "product_qty": line["qty"]}))

    pid = odoo("purchase.order", "create",
               [{"partner_id": vendor["id"], "order_line": order_line}])
    po = odoo("purchase.order", "read", [[pid]], {"fields": ["name"]})
    name = po[0]["name"] if po else "?"
    return f"Đã tạo RFQ {name} cho {vendor['name']} ({len(lines)} dòng)."


@mcp.tool()
def inventory_adjustment(product_name: str, new_qty: float,
                         location_name: str | None = None) -> str:
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
