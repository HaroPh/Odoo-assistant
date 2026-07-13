import json

import helpers


def test_resolve_unique_no_match():
    row, msg = helpers.resolve_unique([], "khách hàng", describe=lambda r: r["name"])
    assert row is None
    assert "không tìm thấy" in msg.lower()


def test_resolve_unique_single_match():
    rows = [{"id": 1, "name": "Azur"}]
    row, msg = helpers.resolve_unique(rows, "khách hàng", describe=lambda r: r["name"])
    assert row == rows[0]
    assert msg is None


def test_resolve_unique_multiple_matches_lists_candidates():
    rows = [{"id": 1, "name": "Azur A"}, {"id": 2, "name": "Azur B"}]
    row, msg = helpers.resolve_unique(rows, "khách hàng",
                                      describe=lambda r: r["name"], hint="nêu rõ hơn")
    assert row is None
    assert "Azur A" in msg and "Azur B" in msg
    assert "nêu rõ hơn" in msg


def test_envelope_roundtrips_via_json():
    out = helpers.envelope(True, "Đã xác nhận đơn S001.", ref="S001",
                           model="sale.order", res_id=7, state="sale")
    data = json.loads(out)
    assert data == {"ok": True, "ref": "S001", "model": "sale.order",
                    "res_id": 7, "state": "sale", "display": "Đã xác nhận đơn S001."}


def test_now_iso_and_today_iso_format():
    assert len(helpers.now_iso()) == 19       # "YYYY-MM-DD HH:MM:SS"
    assert len(helpers.today_iso()) == 10     # "YYYY-MM-DD"
