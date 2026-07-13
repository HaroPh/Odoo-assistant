"""Mirror master data (product.product) từ Odoo vào erp_entity_index.
Full re-sync mỗi lần chạy (vài trăm SKU = 1-2 batch Ollama — đơn giản thắng
tối ưu). Chạy: python -m src.erp_query.sync_index  (CWD backend/, cần
DATABASE_URL + ODOO_* trong env — như rag ingest). Chỉ ĐỌC Odoo qua Gateway;
ghi duy nhất vào PG của project."""
import os

from ..rag import db as _db
from ..rag import embed as _embed
from ..rag.config import RAG_SCHEMA
from .gateway import default_gateway
from .semantic import normalize

_SCHEMA_SQL = os.path.join(os.path.dirname(__file__), "schema.sql")
MODEL = "product.product"
BATCH = 100  # = gateway.MAX_LIMIT


def ensure_schema(conn) -> None:
    with open(_SCHEMA_SQL, encoding="utf-8") as f:
        conn.execute(f.read().format(schema=RAG_SCHEMA))


def fetch_products(gw) -> list[dict]:
    """Watermark paging id > last — Gateway không có offset, giữ nguyên nó."""
    out, last = [], 0
    while True:
        rows = gw.search_read(MODEL, [["id", ">", last]],
                              ["display_name", "default_code", "categ_id"],
                              order="id asc", limit=BATCH)
        if not rows:
            return out
        out.extend(rows)
        last = rows[-1]["id"]


def build_rows(products: list[dict]) -> list[tuple]:
    """(model, odoo_id, name, search_text, embed_text). embed_text giữ dấu tự
    nhiên (bge-m3 xử lý tiếng Việt chuẩn tốt hơn); search_text normalize cho
    trigram."""
    rows = []
    for p in products:
        code = p.get("default_code") or ""
        categ = p["categ_id"][1] if p.get("categ_id") else ""
        raw = " ".join(x for x in (p["display_name"], code, categ) if x).strip()
        rows.append((MODEL, p["id"], p["display_name"], normalize(raw), raw))
    return rows


def sync(gw=None, conn=None) -> dict:
    gw = gw or default_gateway()
    own = conn is None
    if own:
        conn = _db.connect()
    try:
        ensure_schema(conn)
        products = fetch_products(gw)
        if not products:
            # Fetch rỗng có thể là Odoo lỗi giữa chừng — KHÔNG xóa (tránh wipe
            # index vì sự cố tạm thời).
            print("Cảnh báo: fetch 0 sản phẩm — bỏ qua bước xóa mirror.")
            return {"fetched": 0, "upserted": 0, "deleted": 0}
        rows = build_rows(products)
        vecs = _embed.embed_texts([r[4] for r in rows])
        for (model, oid, name, stext, _), vec in zip(rows, vecs):
            conn.execute(
                "INSERT INTO erp_entity_index "
                "(model, odoo_id, name, search_text, embedding, synced_at) "
                "VALUES (%s, %s, %s, %s, %s, now()) "
                "ON CONFLICT (model, odoo_id) DO UPDATE SET "
                "name = EXCLUDED.name, search_text = EXCLUDED.search_text, "
                "embedding = EXCLUDED.embedding, synced_at = now()",
                (model, oid, name, stext, vec))
        deleted = conn.execute(
            "DELETE FROM erp_entity_index "
            "WHERE model = %s AND NOT (odoo_id = ANY(%s))",
            (MODEL, [r[1] for r in rows])).rowcount
        return {"fetched": len(products), "upserted": len(rows),
                "deleted": deleted}
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    print(sync())
