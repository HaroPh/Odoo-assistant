"""CRM bounded context — leads/opportunities. crm.lead KHÔNG nằm trong
MODEL_DENYLIST của gateway (đã verify); res.users NẰM TRONG denylist nên
resolve assignee ở MCP-side (server.py), không ở đây."""
from .envelope import ok, err
from .gateway import default_gateway
from .resolve import resolve_entity


def find_lead(name, *, gw=None):
    return resolve_entity("crm.lead", name, gw=gw)


def find_lead_duplicates(email=None, phone=None, *, gw=None):
    """Dup-check tất định cho create_lead (SOP1: 'tránh trùng lặp'). Chỉ check
    khi có email/phone; không có gì → rows rỗng, KHÔNG chạm gateway."""
    conds = []
    if str(email or "").strip():
        conds.append(["email_from", "ilike", email])
    if str(phone or "").strip():
        conds.append(["phone", "ilike", phone])
    if not conds:
        return ok({"rows": []}, "Không có email/SĐT để kiểm tra trùng.")
    domain = (["|"] + conds) if len(conds) == 2 else conds
    gw = gw or default_gateway()
    try:
        rows = gw.search_read("crm.lead", domain, ["name", "type"], limit=5)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi kiểm tra lead trùng: {e}")
    return ok({"rows": rows},
              f"{len(rows)} lead trùng email/SĐT." if rows else "Không trùng.")


def list_crm_leads(kind=None, stage=None, limit=50, *, gw=None):
    gw = gw or default_gateway()
    domain = []
    if kind:
        domain.append(["type", "=", kind])
    if stage:
        domain.append(["stage_id.name", "ilike", stage])
    try:
        rows = gw.search_read("crm.lead", domain,
                              ["name", "type", "contact_name", "partner_name",
                               "stage_id", "user_id", "expected_revenue"],
                              order="id desc", limit=limit)
    except Exception as e:                                  # noqa: BLE001
        return err(f"Lỗi tra cứu lead/cơ hội: {e}")
    if not rows:
        return ok({"rows": [], "count": 0}, "Chưa có lead/cơ hội nào phù hợp.")
    lines = [f"{r['name']} | {'lead' if r['type'] == 'lead' else 'cơ hội'} "
             f"| {r['contact_name'] or r['partner_name'] or '—'} "
             f"| {(r['stage_id'] or [0, '—'])[1]} "
             f"| {(r['user_id'] or [0, '—'])[1]}" for r in rows]
    return ok({"rows": rows, "count": len(rows)},
              f"{len(rows)} lead/cơ hội:\n" + "\n".join(lines))
