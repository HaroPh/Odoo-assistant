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


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="sse")
