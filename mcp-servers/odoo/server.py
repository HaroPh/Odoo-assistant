"""
Odoo MCP Server — Phase 1: Read-Only
Odoo 19 field names (validated via discovery script 2026-06-23).

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
def get_late_orders(limit: int = 50) -> str:
    """
    Lấy các đơn bán hàng đang trễ: đã xác nhận, có ngày giao (commitment_date),
    chưa giao đủ, và ngày giao đã qua hôm nay.
    """
    now = now_iso()
    domain = [
        ["state", "in", ["sale", "done"]],
        ["commitment_date", "<", now],
        ["delivery_status", "!=", "full"],
    ]
    rows = odoo("sale.order", "search_read", [domain], {
        "fields": ["name", "partner_id", "date_order", "commitment_date",
                   "delivery_status", "amount_total", "state"],
        "limit": limit,
        "order": "commitment_date asc",
    })
    if not rows:
        return "Không có đơn hàng nào đang trễ."
    lines = [f"Ngày hiện tại: {today_iso()} — {len(rows)} đơn trễ:\n"]
    for r in rows:
        partner = r["partner_id"][1] if r["partner_id"] else "N/A"
        commit  = (r.get("commitment_date") or "N/A")[:10]
        lines.append(
            f"  {r['name']} | Khách: {partner} | Ngày giao: {commit} "
            f"| Trạng thái giao: {r['delivery_status']} | Tổng: {r['amount_total']:,.0f}"
        )
    return "\n".join(lines)


@mcp.tool()
def search_orders(
    state: str | None = None,
    partner_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> str:
    """
    Tìm kiếm đơn bán hàng (sale.order).

    Args:
        state: Trạng thái — draft | sent | sale | done | cancel (bỏ trống = tất cả)
        partner_name: Tên khách hàng (tìm gần đúng)
        date_from: Ngày đặt hàng từ (YYYY-MM-DD)
        date_to: Ngày đặt hàng đến (YYYY-MM-DD)
        limit: Số dòng tối đa (mặc định 50)
    """
    domain: list = []
    if state:
        domain.append(["state", "=", state])
    if partner_name:
        domain.append(["partner_id.name", "ilike", partner_name])
    if date_from:
        domain.append(["date_order", ">=", date_from + " 00:00:00"])
    if date_to:
        domain.append(["date_order", "<=", date_to + " 23:59:59"])

    rows = odoo("sale.order", "search_read", [domain], {
        "fields": ["name", "partner_id", "date_order", "commitment_date",
                   "state", "amount_total", "delivery_status"],
        "limit": limit,
        "order": "date_order desc",
    })
    if not rows:
        return "Không tìm thấy đơn hàng nào phù hợp."
    lines = [f"Tìm thấy {len(rows)} đơn hàng (hiển thị tối đa {limit}):\n"]
    for r in rows:
        partner = r["partner_id"][1] if r["partner_id"] else "N/A"
        date_o  = (r.get("date_order") or "N/A")[:10]
        lines.append(
            f"  {r['name']} | {partner} | Ngày: {date_o} "
            f"| Trạng thái: {r['state']} | Tổng: {r['amount_total']:,.0f}"
        )
    return "\n".join(lines)


@mcp.tool()
def get_inventory(
    product_name: str | None = None,
    low_stock_threshold: float | None = None,
    limit: int = 100,
) -> str:
    """
    Xem tồn kho hiện tại (stock.quant, chỉ kho nội bộ).

    Args:
        product_name: Lọc theo tên sản phẩm (tìm gần đúng)
        low_stock_threshold: Nếu set, chỉ trả sản phẩm có available_quantity <= ngưỡng này
        limit: Số dòng tối đa
    """
    domain: list = [["location_id.usage", "=", "internal"]]
    if product_name:
        domain.append(["product_id.name", "ilike", product_name])

    # available_quantity là computed (không stored) → KHÔNG filter/order trong SQL.
    # Khi cần lọc theo ngưỡng: fetch rộng rồi lọc + cắt limit trong Python.
    fetch_limit = 1000 if low_stock_threshold is not None else limit
    rows = odoo("stock.quant", "search_read", [domain], {
        "fields": ["product_id", "location_id", "quantity",
                   "reserved_quantity", "available_quantity", "product_uom_id"],
        "limit": fetch_limit,
        "order": "product_id asc",
    })
    if low_stock_threshold is not None:
        rows = [r for r in rows if (r.get("available_quantity") or 0) <= low_stock_threshold]
    rows = rows[:limit]
    if not rows:
        return "Không có dữ liệu tồn kho phù hợp."
    lines = [f"Tồn kho nội bộ — {len(rows)} dòng:\n"]
    for r in rows:
        product  = r["product_id"][1] if r["product_id"] else "N/A"
        location = r["location_id"][1] if r["location_id"] else "N/A"
        uom      = r["product_uom_id"][1] if r["product_uom_id"] else ""
        lines.append(
            f"  {product:40s} | Kho: {location} "
            f"| Có: {r['available_quantity']:.1f} {uom} "
            f"(Tổng: {r['quantity']:.1f}, Đặt trước: {r['reserved_quantity']:.1f})"
        )
    return "\n".join(lines)


@mcp.tool()
def search_customers(
    name: str | None = None,
    limit: int = 50,
) -> str:
    """
    Tìm khách hàng (res.partner có customer_rank > 0).

    Args:
        name: Tên khách hàng (tìm gần đúng, bỏ trống = tất cả)
        limit: Số dòng tối đa
    """
    domain: list = [["customer_rank", ">", 0]]
    if name:
        domain.append(["name", "ilike", name])

    rows = odoo("res.partner", "search_read", [domain], {
        "fields": ["name", "email", "phone", "customer_rank", "sale_order_count"],
        "limit": limit,
        "order": "customer_rank desc, name asc",  # sale_order_count là computed, không order được
    })
    if not rows:
        return "Không tìm thấy khách hàng."
    lines = [f"Tìm thấy {len(rows)} khách hàng:\n"]
    for r in rows:
        lines.append(
            f"  {r['name']:35s} | Email: {r.get('email') or 'N/A':30s} "
            f"| Số đơn: {r['sale_order_count']}"
        )
    return "\n".join(lines)


@mcp.tool()
def search_suppliers(
    name: str | None = None,
    limit: int = 50,
) -> str:
    """
    Tìm nhà cung cấp (res.partner có supplier_rank > 0).

    Args:
        name: Tên nhà cung cấp (bỏ trống = tất cả)
        limit: Số dòng tối đa
    """
    domain: list = [["supplier_rank", ">", 0]]
    if name:
        domain.append(["name", "ilike", name])

    rows = odoo("res.partner", "search_read", [domain], {
        "fields": ["name", "email", "phone", "supplier_rank", "purchase_order_count"],
        "limit": limit,
        "order": "supplier_rank desc, name asc",  # purchase_order_count là computed
    })
    if not rows:
        return "Không tìm thấy nhà cung cấp."
    lines = [f"Tìm thấy {len(rows)} nhà cung cấp:\n"]
    for r in rows:
        lines.append(
            f"  {r['name']:35s} | Email: {r.get('email') or 'N/A':30s} "
            f"| Số PO: {r['purchase_order_count']}"
        )
    return "\n".join(lines)


@mcp.tool()
def search_purchase_orders(
    state: str | None = None,
    vendor_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
) -> str:
    """
    Tìm đơn mua hàng (purchase.order).

    Args:
        state: draft | sent | purchase | done | cancel (bỏ trống = tất cả)
        vendor_name: Tên nhà cung cấp (tìm gần đúng)
        date_from: Ngày đặt từ (YYYY-MM-DD)
        date_to: Ngày đặt đến (YYYY-MM-DD)
        limit: Số dòng tối đa
    """
    domain: list = []
    if state:
        domain.append(["state", "=", state])
    if vendor_name:
        domain.append(["partner_id.name", "ilike", vendor_name])
    if date_from:
        domain.append(["date_order", ">=", date_from + " 00:00:00"])
    if date_to:
        domain.append(["date_order", "<=", date_to + " 23:59:59"])

    rows = odoo("purchase.order", "search_read", [domain], {
        "fields": ["name", "partner_id", "date_order", "date_planned",
                   "state", "amount_total", "receipt_status"],
        "limit": limit,
        "order": "date_order desc",
    })
    if not rows:
        return "Không tìm thấy đơn mua hàng nào phù hợp."
    lines = [f"Tìm thấy {len(rows)} đơn mua (hiển thị tối đa {limit}):\n"]
    for r in rows:
        vendor     = r["partner_id"][1] if r["partner_id"] else "N/A"
        date_o     = (r.get("date_order") or "N/A")[:10]
        date_plan  = (r.get("date_planned") or "N/A")[:10]
        lines.append(
            f"  {r['name']} | {vendor} | Đặt: {date_o} | Dự kiến nhận: {date_plan} "
            f"| Trạng thái: {r['state']} | Tổng: {r['amount_total']:,.0f}"
        )
    return "\n".join(lines)


@mcp.tool()
def get_sales_summary(period: str = "month") -> str:
    """
    Tổng hợp doanh thu theo kỳ.

    Args:
        period: month | quarter | year (tính đến hôm nay)
    """
    today = datetime.now(timezone.utc)
    if period == "month":
        date_from = today.replace(day=1).strftime("%Y-%m-%d")
    elif period == "quarter":
        q_start_month = ((today.month - 1) // 3) * 3 + 1
        date_from = today.replace(month=q_start_month, day=1).strftime("%Y-%m-%d")
    else:  # year
        date_from = today.replace(month=1, day=1).strftime("%Y-%m-%d")

    domain = [
        ["state", "in", ["sale", "done"]],
        ["date_order", ">=", date_from + " 00:00:00"],
    ]
    rows = odoo("sale.order", "search_read", [domain], {
        "fields": ["name", "partner_id", "amount_total", "date_order"],
        "limit": 500,
    })

    if not rows:
        return f"Không có đơn hàng xác nhận nào từ {date_from} đến nay."

    total = sum(r["amount_total"] for r in rows)

    # Group by partner
    by_partner: dict[str, float] = {}
    for r in rows:
        p = r["partner_id"][1] if r["partner_id"] else "Unknown"
        by_partner[p] = by_partner.get(p, 0) + r["amount_total"]

    top5 = sorted(by_partner.items(), key=lambda x: x[1], reverse=True)[:5]

    lines = [
        f"Doanh thu {period} (từ {date_from} đến {today_iso()}):",
        f"  Tổng đơn: {len(rows)}",
        f"  Tổng doanh thu: {total:,.0f}",
        "",
        "  Top 5 khách hàng:",
    ]
    for name, amt in top5:
        lines.append(f"    {name:35s}: {amt:,.0f}")

    return "\n".join(lines)


def _period_date_from(period: str | None) -> str | None:
    """period → ngày bắt đầu (YYYY-MM-DD). None = không lọc thời gian."""
    if not period:
        return None
    today = datetime.now(timezone.utc)
    if period == "month":
        return today.replace(day=1).strftime("%Y-%m-%d")
    if period == "quarter":
        q = ((today.month - 1) // 3) * 3 + 1
        return today.replace(month=q, day=1).strftime("%Y-%m-%d")
    if period == "year":
        return today.replace(month=1, day=1).strftime("%Y-%m-%d")
    return None


@mcp.tool()
def get_top_products(by: str = "quantity", period: str | None = None,
                     limit: int = 10) -> str:
    """
    Sản phẩm bán chạy nhất (từ dòng đơn bán, chỉ đơn đã xác nhận).

    Args:
        by: "quantity" (theo số lượng bán) hoặc "revenue" (theo doanh thu)
        period: None|month|quarter|year — lọc theo ngày đặt, tính đến hôm nay
        limit: số sản phẩm top (mặc định 10)
    """
    domain: list = [["order_id.state", "in", ["sale", "done"]]]
    date_from = _period_date_from(period)
    if date_from:
        domain.append(["order_id.date_order", ">=", date_from + " 00:00:00"])

    orderby = "price_subtotal desc" if by == "revenue" else "product_uom_qty desc"
    rows = odoo("sale.order.line", "read_group",
                [domain, ["product_uom_qty:sum", "price_subtotal:sum"], ["product_id"]],
                {"orderby": orderby, "limit": limit, "lazy": False})
    if not rows:
        return "Không có dữ liệu bán hàng phù hợp."

    metric = "doanh thu" if by == "revenue" else "số lượng"
    scope = f" ({period})" if period else ""
    lines = [f"Top {len(rows)} sản phẩm bán chạy theo {metric}{scope}:\n"]
    for i, r in enumerate(rows, 1):
        product = r["product_id"][1] if r.get("product_id") else "N/A"
        qty     = r.get("product_uom_qty") or 0
        revenue = r.get("price_subtotal") or 0
        lines.append(
            f"  {i:2d}. {product:42s} | SL bán: {qty:,.0f} | Doanh thu: {revenue:,.0f}"
        )
    return "\n".join(lines)


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


# ─── READ TOOLS (T1 expansion) ────────────────────────────────────────────────

@mcp.tool()
def get_customer_invoices(partner_name: str | None = None,
                          payment_state: str | None = None,
                          limit: int = 50) -> str:
    """Hóa đơn khách hàng (account.move, out_invoice đã phát hành).

    Args:
        partner_name: Lọc theo tên khách (tìm gần đúng).
        payment_state: not_paid | in_payment | partial | paid | reversed.
        limit: Số dòng tối đa.
    """
    domain = [["move_type", "=", "out_invoice"], ["state", "=", "posted"]]
    if partner_name:
        domain.append(["partner_id.name", "ilike", partner_name])
    if payment_state:
        domain.append(["payment_state", "=", payment_state])
    rows = odoo("account.move", "search_read", [domain], {
        "fields": ["name", "partner_id", "invoice_date", "invoice_date_due",
                   "amount_total", "amount_residual", "payment_state"],
        "limit": limit, "order": "invoice_date desc",
    })
    if not rows:
        return "Không có hóa đơn khách hàng nào phù hợp."
    lines = [f"{len(rows)} hóa đơn khách hàng:\n"]
    for r in rows:
        partner = r["partner_id"][1] if r["partner_id"] else "N/A"
        lines.append(
            f"  {r['name']} | {partner} | Ngày: {r.get('invoice_date') or 'N/A'} "
            f"| Tổng: {r['amount_total']:,.0f} | Còn nợ: {r['amount_residual']:,.0f} "
            f"| TT: {r['payment_state']}")
    return "\n".join(lines)


@mcp.tool()
def get_vendor_bills(vendor_name: str | None = None,
                     payment_state: str | None = None,
                     limit: int = 50) -> str:
    """Hóa đơn nhà cung cấp (account.move, in_invoice đã phát hành).

    Args:
        vendor_name: Lọc theo tên nhà cung cấp (tìm gần đúng).
        payment_state: not_paid | in_payment | partial | paid | reversed.
        limit: Số dòng tối đa.
    """
    domain = [["move_type", "=", "in_invoice"], ["state", "=", "posted"]]
    if vendor_name:
        domain.append(["partner_id.name", "ilike", vendor_name])
    if payment_state:
        domain.append(["payment_state", "=", payment_state])
    rows = odoo("account.move", "search_read", [domain], {
        "fields": ["name", "partner_id", "invoice_date", "invoice_date_due",
                   "amount_total", "amount_residual", "payment_state"],
        "limit": limit, "order": "invoice_date desc",
    })
    if not rows:
        return "Không có hóa đơn nhà cung cấp nào phù hợp."
    lines = [f"{len(rows)} hóa đơn nhà cung cấp:\n"]
    for r in rows:
        partner = r["partner_id"][1] if r["partner_id"] else "N/A"
        lines.append(
            f"  {r['name']} | {partner} | Ngày: {r.get('invoice_date') or 'N/A'} "
            f"| Tổng: {r['amount_total']:,.0f} | Còn nợ: {r['amount_residual']:,.0f} "
            f"| TT: {r['payment_state']}")
    return "\n".join(lines)


@mcp.tool()
def get_overdue_invoices(limit: int = 50) -> str:
    """Hóa đơn khách hàng quá hạn (chưa trả hết, đến hạn đã qua)."""
    today = today_iso()
    domain = [
        ["move_type", "=", "out_invoice"], ["state", "=", "posted"],
        ["payment_state", "in", ["not_paid", "partial"]],
        ["invoice_date_due", "<", today],
    ]
    rows = odoo("account.move", "search_read", [domain], {
        "fields": ["name", "partner_id", "invoice_date_due",
                   "amount_total", "amount_residual"],
        "limit": limit, "order": "invoice_date_due asc",
    })
    if not rows:
        return "Không có hóa đơn nào quá hạn."
    lines = [f"Ngày hiện tại: {today} — {len(rows)} hóa đơn quá hạn:\n"]
    for r in rows:
        partner = r["partner_id"][1] if r["partner_id"] else "N/A"
        lines.append(
            f"  {r['name']} | {partner} | Đến hạn: {r.get('invoice_date_due') or 'N/A'} "
            f"| Còn nợ: {r['amount_residual']:,.0f} / {r['amount_total']:,.0f}")
    return "\n".join(lines)


def _format_pickings(rows, title: str) -> str:
    if not rows:
        return f"Không có {title} nào phù hợp."
    lines = [f"{len(rows)} {title}:\n"]
    for r in rows:
        partner = r["partner_id"][1] if r.get("partner_id") else "N/A"
        sched = (r.get("scheduled_date") or "N/A")[:16]
        lines.append(
            f"  {r['name']} | {partner} | Dự kiến: {sched} "
            f"| Trạng thái: {r['state']} | Nguồn: {r.get('origin') or '-'}")
    return "\n".join(lines)


_PICKING_FIELDS = ["name", "partner_id", "scheduled_date", "state", "origin"]


@mcp.tool()
def get_deliveries(state: str | None = None, partner_name: str | None = None,
                   limit: int = 50) -> str:
    """Phiếu giao hàng (stock.picking, outgoing).

    Args:
        state: draft|waiting|confirmed|assigned|done|cancel (bỏ trống = tất cả).
        partner_name: Lọc theo tên khách (tìm gần đúng).
        limit: Số dòng tối đa.
    """
    domain = [["picking_type_code", "=", "outgoing"]]
    if state:
        domain.append(["state", "=", state])
    if partner_name:
        domain.append(["partner_id.name", "ilike", partner_name])
    rows = odoo("stock.picking", "search_read", [domain],
                {"fields": _PICKING_FIELDS, "limit": limit,
                 "order": "scheduled_date desc"})
    return _format_pickings(rows, "phiếu giao hàng")


@mcp.tool()
def get_receipts(state: str | None = None, limit: int = 50) -> str:
    """Phiếu nhận hàng (stock.picking, incoming).

    Args:
        state: draft|waiting|confirmed|assigned|done|cancel (bỏ trống = tất cả).
        limit: Số dòng tối đa.
    """
    domain = [["picking_type_code", "=", "incoming"]]
    if state:
        domain.append(["state", "=", state])
    rows = odoo("stock.picking", "search_read", [domain],
                {"fields": _PICKING_FIELDS, "limit": limit,
                 "order": "scheduled_date desc"})
    return _format_pickings(rows, "phiếu nhận hàng")


@mcp.tool()
def get_internal_transfers(state: str | None = None, limit: int = 50) -> str:
    """Phiếu điều chuyển nội bộ (stock.picking, internal).

    Args:
        state: draft|waiting|confirmed|assigned|done|cancel (bỏ trống = tất cả).
        limit: Số dòng tối đa.
    """
    domain = [["picking_type_code", "=", "internal"]]
    if state:
        domain.append(["state", "=", state])
    rows = odoo("stock.picking", "search_read", [domain],
                {"fields": _PICKING_FIELDS, "limit": limit,
                 "order": "scheduled_date desc"})
    return _format_pickings(rows, "phiếu điều chuyển nội bộ")


@mcp.tool()
def search_lots(product_name: str | None = None, limit: int = 50) -> str:
    """Tra cứu Lô / Số sê-ri (stock.lot) và tồn hiện tại.

    Args:
        product_name: Lọc theo tên sản phẩm (tìm gần đúng).
        limit: Số dòng tối đa.
    """
    domain: list = []
    if product_name:
        domain.append(["product_id.name", "ilike", product_name])
    rows = odoo("stock.lot", "search_read", [domain],
                {"fields": ["name", "product_id", "product_qty"],
                 "limit": limit, "order": "product_id asc"})
    if not rows:
        return "Không tìm thấy lô/sê-ri nào phù hợp."
    lines = [f"{len(rows)} lô/sê-ri:\n"]
    for r in rows:
        product = r["product_id"][1] if r.get("product_id") else "N/A"
        lines.append(f"  {r['name']:20s} | SP: {product:35s} | Tồn: {r['product_qty']:.1f}")
    return "\n".join(lines)


@mcp.tool()
def search_products(name: str | None = None, limit: int = 50) -> str:
    """Tra cứu sản phẩm (product.product): giá bán, giá vốn, tồn kho.

    Args:
        name: Tìm theo tên hoặc mã nội bộ (tìm gần đúng).
        limit: Số dòng tối đa.
    """
    domain: list = []
    if name:
        domain = ["|", ["name", "ilike", name], ["default_code", "ilike", name]]
    rows = odoo("product.product", "search_read", [domain],
                {"fields": ["name", "default_code", "list_price",
                            "standard_price", "qty_available", "uom_id"],
                 "limit": limit, "order": "name asc"})
    if not rows:
        return "Không tìm thấy sản phẩm nào phù hợp."
    lines = [f"{len(rows)} sản phẩm:\n"]
    for r in rows:
        uom = r["uom_id"][1] if r.get("uom_id") else ""
        lines.append(
            f"  [{r.get('default_code') or '-'}] {r['name']:35s} "
            f"| Giá bán: {r['list_price']:,.0f} | Giá vốn: {r['standard_price']:,.0f} "
            f"| Tồn: {r['qty_available']:.1f} {uom}")
    return "\n".join(lines)


@mcp.tool()
def get_sale_order_detail(order_ref: str) -> str:
    """Chi tiết dòng sản phẩm của một đơn bán (sale.order).

    Args:
        order_ref: Mã đơn bán, ví dụ "S00012".
    """
    orders = odoo("sale.order", "search_read", [[["name", "=", order_ref]]],
                  {"fields": ["id", "name", "partner_id", "amount_total"], "limit": 2})
    if not orders:
        return f"Không tìm thấy đơn '{order_ref}'."
    if len(orders) > 1:
        return f"Có nhiều đơn tên '{order_ref}'. Vui lòng nêu rõ hơn."
    o = orders[0]
    rows = odoo("sale.order.line", "search_read", [[["order_id", "=", o["id"]]]],
                {"fields": ["product_id", "product_uom_qty", "price_unit",
                            "price_subtotal"], "order": "id asc"})
    partner = o["partner_id"][1] if o["partner_id"] else "N/A"
    out = [f"Đơn {o['name']} | Khách: {partner} | Tổng: {o['amount_total']:,.0f}\n"]
    if not rows:
        out.append("  (không có dòng sản phẩm)")
    for ln in rows:
        product = ln["product_id"][1] if ln.get("product_id") else "N/A"
        out.append(
            f"  {product:35s} | SL: {ln['product_uom_qty']:.1f} "
            f"| Đơn giá: {ln['price_unit']:,.0f} | Thành tiền: {ln['price_subtotal']:,.0f}")
    return "\n".join(out)


@mcp.tool()
def get_purchase_order_detail(order_ref: str) -> str:
    """Chi tiết dòng sản phẩm của một đơn mua (purchase.order).

    Args:
        order_ref: Mã đơn mua, ví dụ "P00003".
    """
    orders = odoo("purchase.order", "search_read", [[["name", "=", order_ref]]],
                  {"fields": ["id", "name", "partner_id", "amount_total"], "limit": 2})
    if not orders:
        return f"Không tìm thấy đơn '{order_ref}'."
    if len(orders) > 1:
        return f"Có nhiều đơn tên '{order_ref}'. Vui lòng nêu rõ hơn."
    o = orders[0]
    rows = odoo("purchase.order.line", "search_read", [[["order_id", "=", o["id"]]]],
                {"fields": ["product_id", "product_qty", "price_unit",
                            "price_subtotal"], "order": "id asc"})
    partner = o["partner_id"][1] if o["partner_id"] else "N/A"
    out = [f"Đơn {o['name']} | NCC: {partner} | Tổng: {o['amount_total']:,.0f}\n"]
    if not rows:
        out.append("  (không có dòng sản phẩm)")
    for ln in rows:
        product = ln["product_id"][1] if ln.get("product_id") else "N/A"
        out.append(
            f"  {product:35s} | SL: {ln['product_qty']:.1f} "
            f"| Đơn giá: {ln['price_unit']:,.0f} | Thành tiền: {ln['price_subtotal']:,.0f}")
    return "\n".join(out)


@mcp.tool()
def search_leads(type: str | None = None, salesperson: str | None = None,
                 limit: int = 50) -> str:
    """Tra cứu Lead / Cơ hội (crm.lead).

    Args:
        type: "lead" hoặc "opportunity" (bỏ trống = cả hai).
        salesperson: Lọc theo tên nhân viên phụ trách (tìm gần đúng).
        limit: Số dòng tối đa.
    """
    domain: list = []
    if type:
        domain.append(["type", "=", type])
    if salesperson:
        domain.append(["user_id.name", "ilike", salesperson])
    rows = odoo("crm.lead", "search_read", [domain], {
        "fields": ["name", "contact_name", "email_from", "stage_id",
                   "expected_revenue", "probability", "user_id", "type"],
        "limit": limit, "order": "expected_revenue desc",
    })
    if not rows:
        return "Không tìm thấy lead/cơ hội nào phù hợp."
    lines = [f"{len(rows)} lead/cơ hội:\n"]
    for r in rows:
        stage = r["stage_id"][1] if r.get("stage_id") else "N/A"
        sp = r["user_id"][1] if r.get("user_id") else "N/A"
        lines.append(
            f"  {r['name']} | LH: {r.get('contact_name') or '-'} | GĐ: {stage} "
            f"| Dự kiến: {r['expected_revenue']:,.0f} ({r['probability']:.0f}%) | NV: {sp}")
    return "\n".join(lines)


@mcp.tool()
def get_manufacturing_orders(state: str | None = None, limit: int = 50) -> str:
    """Lệnh sản xuất (mrp.production).

    Args:
        state: draft|confirmed|progress|to_close|done|cancel (bỏ trống = tất cả).
        limit: Số dòng tối đa.
    """
    domain: list = []
    if state:
        domain.append(["state", "=", state])
    rows = odoo("mrp.production", "search_read", [domain], {
        "fields": ["name", "product_id", "product_qty", "state", "date_start"],
        "limit": limit, "order": "date_start desc",
    })
    if not rows:
        return "Không có lệnh sản xuất nào phù hợp."
    lines = [f"{len(rows)} lệnh sản xuất:\n"]
    for r in rows:
        product = r["product_id"][1] if r.get("product_id") else "N/A"
        start = (r.get("date_start") or "N/A")[:16]
        lines.append(
            f"  {r['name']} | SP: {product:30s} | SL: {r['product_qty']:.1f} "
            f"| Trạng thái: {r['state']} | Bắt đầu: {start}")
    return "\n".join(lines)


@mcp.tool()
def get_bom(product_name: str) -> str:
    """Định mức nguyên vật liệu (BOM) của một sản phẩm.

    Args:
        product_name: Tên sản phẩm cần xem BOM (tìm gần đúng).
    """
    boms = odoo("mrp.bom", "search_read",
                [[["product_tmpl_id.name", "ilike", product_name]]],
                {"fields": ["id", "code", "product_tmpl_id", "product_qty"], "limit": 5})
    if not boms:
        return f"Không tìm thấy BOM cho '{product_name}'."
    if len(boms) > 1:
        names = ", ".join((b["product_tmpl_id"][1] if b["product_tmpl_id"] else "?")
                          for b in boms)
        return f"Có nhiều BOM khớp '{product_name}': {names}. Vui lòng nêu rõ hơn."
    b = boms[0]
    comps = odoo("mrp.bom.line", "search_read", [[["bom_id", "=", b["id"]]]],
                 {"fields": ["product_id", "product_qty"], "order": "id asc"})
    product = b["product_tmpl_id"][1] if b["product_tmpl_id"] else "N/A"
    out = [f"BOM: {product} (tạo {b['product_qty']:.0f} đơn vị)\n  Thành phần:"]
    if not comps:
        out.append("    (không có thành phần)")
    for c in comps:
        cp = c["product_id"][1] if c.get("product_id") else "N/A"
        out.append(f"    - {cp:35s} x {c['product_qty']:.2f}")
    return "\n".join(out)


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="sse")
