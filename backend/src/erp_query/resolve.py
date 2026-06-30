"""Entity resolution via Odoo name_search. Reports FACTS (candidate matches +
a needs_disambiguation flag); it never picks or prompts — that policy lives in
orchestration (C)."""
from difflib import SequenceMatcher

from .envelope import ok, err
from .gateway import default_gateway


def _score(query: str, name: str) -> float:
    return round(SequenceMatcher(None, query.strip().lower(), (name or "").lower()).ratio(), 2)


def resolve_entity(model: str, query: str, limit: int = 5, *, gw=None) -> dict:
    gw = gw or default_gateway()
    try:
        rows = gw.name_search(model, query, limit=limit)   # [(id, display_name), ...]
    except Exception as e:                                  # noqa: BLE001 — fail safe
        return err(f"Lỗi tra cứu {model}: {e}")
    matches = [{"id": rid, "name": name, "score": _score(query, name)} for rid, name in rows]
    exact = [m for m in matches if m["name"].strip().lower() == query.strip().lower()]
    needs = len(matches) > 1 and len(exact) != 1
    if not matches:
        display = f"Không tìm thấy '{query}'."
    elif not needs:
        chosen = exact[0] if exact else matches[0]
        display = f"Đã xác định: {chosen['name']} (ID {chosen['id']})."
    else:
        listing = "; ".join(f"{m['name']} (ID {m['id']})" for m in matches)
        display = f"Có nhiều kết quả cho '{query}': {listing}."
    return ok({"matches": matches, "needs_disambiguation": needs}, display)
