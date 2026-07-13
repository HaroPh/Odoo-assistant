import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
from backend.src.erp_query import sync_index


class FakeGw:
    def __init__(self, batches): self.batches = list(batches); self.calls = []
    def search_read(self, model, domain, fields, order=None, limit=50, context=None):
        self.calls.append((model, domain, order, limit))
        return self.batches.pop(0)


class FakeConn:
    def __init__(self): self.executed = []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        class R: rowcount = 3
        return R()
    def close(self): pass


def test_fetch_products_watermark_paging():
    b1 = [{"id": i, "display_name": f"P{i}", "default_code": None,
           "categ_id": False} for i in range(1, 101)]
    b2 = [{"id": 101, "display_name": "P101", "default_code": "X",
           "categ_id": [3, "Nội thất"]}]
    gw = FakeGw([b1, b2, []])
    out = sync_index.fetch_products(gw)
    assert len(out) == 101
    # watermark id > last — KHÔNG offset (Gateway đóng băng)
    assert gw.calls[0][1] == [["id", ">", 0]]
    assert gw.calls[1][1] == [["id", ">", 100]]
    assert gw.calls[2][1] == [["id", ">", 101]]
    assert all(order == "id asc" for _, _, order, _ in gw.calls)


def test_build_rows_normalizes_search_text_keeps_embed_text_natural():
    rows = sync_index.build_rows(
        [{"id": 7, "display_name": "Đèn bàn LED", "default_code": "VN-DEN-01",
          "categ_id": [3, "Đồ điện"]}])
    model, oid, name, search_text, embed_text = rows[0]
    assert (model, oid, name) == ("product.product", 7, "Đèn bàn LED")
    assert search_text == "den ban led vn-den-01 do dien"   # đã bỏ dấu
    assert embed_text == "Đèn bàn LED VN-DEN-01 Đồ điện"    # giữ dấu cho bge-m3


def test_sync_empty_fetch_deletes_nothing():
    gw = FakeGw([[]])
    conn = FakeConn()
    out = sync_index.sync(gw=gw, conn=conn)
    assert out == {"fetched": 0, "upserted": 0, "deleted": 0}
    assert all("DELETE" not in (sql or "") for sql, _ in conn.executed)


def test_sync_upserts_then_mirror_deletes(monkeypatch):
    gw = FakeGw([[{"id": 7, "display_name": "Ghế xoay", "default_code": None,
                   "categ_id": False}], []])
    conn = FakeConn()
    monkeypatch.setattr(sync_index._embed, "embed_texts",
                        lambda texts: [[0.0] * 1024 for _ in texts])
    out = sync_index.sync(gw=gw, conn=conn)
    assert out == {"fetched": 1, "upserted": 1, "deleted": 3}  # rowcount fake=3
    inserts = [p for sql, p in conn.executed if sql.startswith("INSERT")]
    assert len(inserts) == 1 and inserts[0][1] == 7            # odoo_id
    deletes = [p for sql, p in conn.executed if sql.startswith("DELETE")]
    assert deletes == [("product.product", [7])]
