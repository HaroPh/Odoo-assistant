"""Semantic candidate source cho resolve_entity — trigram + vector + RRF trên
bảng erp_entity_index (mirror bởi sync_index.py). Fail-open: mọi lỗi → None,
caller dùng lexical như cũ. Chỉ đề xuất ID — giá trị sống đọc qua Gateway
(spec 2026-07-13-semantic-entity-resolution)."""
import os
import unicodedata

from ..rag import db as _db
from ..rag import embed as _embed

RRF_K = 60  # cùng hằng số rag/retrieve.py — hằng local, giữ erp_query tự chứa


def normalize(s: str) -> str:
    """lower + bỏ dấu tiếng Việt + gộp khoảng trắng. đ/Đ là codepoint riêng
    (U+0111/U+0110), NFD không tách — phải replace tường minh trước."""
    s = (s or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return " ".join(s.lower().split())


def semantic_candidates(model: str, query: str, k: int = 8) -> list[dict] | None:
    """Ứng viên [{odoo_id, name}] theo RRF trigram+vector giảm dần, tối đa 2k
    phần tử; None khi kill-switch tắt hoặc bất kỳ lỗi nào (fail-open —
    pattern rag/reranker.py). Env đọc mỗi call, không cache."""
    if os.environ.get("ERP_SEMANTIC_RESOLVE", "1") != "1":
        return None
    try:
        conn = _db.connect()
        try:
            tri = conn.execute(
                "SELECT odoo_id, name, similarity(search_text, %s) AS s "
                "FROM erp_entity_index WHERE model = %s "
                "ORDER BY s DESC LIMIT %s",
                (normalize(query), model, k)).fetchall()
            qvec = _embed.embed_query(query)   # query nguyên bản, có dấu
            vec = conn.execute(
                "SELECT odoo_id, name, 1 - (embedding <=> %s::vector) AS s "
                "FROM erp_entity_index "
                "WHERE model = %s AND embedding IS NOT NULL "
                "ORDER BY embedding <=> %s::vector LIMIT %s",
                (qvec, model, qvec, k)).fetchall()
        finally:
            conn.close()
        acc: dict = {}
        for rows in (tri, vec):
            for rank, row in enumerate(rows):
                e = acc.setdefault(row[0], {"odoo_id": row[0], "name": row[1],
                                            "rrf": 0.0})
                e["rrf"] += 1.0 / (RRF_K + rank + 1)
        ranked = sorted(acc.values(), key=lambda e: e["rrf"], reverse=True)
        return [{"odoo_id": e["odoo_id"], "name": e["name"]} for e in ranked]
    except Exception:  # noqa: BLE001 — fail-open toàn tầng (spec §7)
        return None
