import json

import server


def fn(name):
    f = getattr(server, name)
    return getattr(f, "fn", f)


def _env(out):
    data = json.loads(out)
    assert set(data) == {"ok", "ref", "model", "res_id", "state", "display"}
    return data


def _lead(id=45, name="Quan tâm lốp xe", type="lead", partner_id=False,
         active=True):
    return {"id": id, "name": name, "type": type, "partner_id": partner_id,
           "active": active}


def _fake(monkeypatch, lead_rows=None, users=None, act_types=None,
         after_type="opportunity", after_user=(2, "Mitchell Admin")):
    calls = []

    def fake_odoo(model, method, args, kwargs=None, tool_name=None):
        calls.append({"model": model, "method": method,
                      "args": args, "kwargs": kwargs or {}})
        if model == "crm.lead" and method == "create":
            return 45
        if model == "crm.lead" and method == "search_read":
            return lead_rows if lead_rows is not None else [_lead()]
        if model == "crm.lead" and method == "convert_opportunity":
            return True
        if model == "crm.lead" and method == "write":
            return True
        if model == "crm.lead" and method == "read":
            return [{"id": 45, "name": "Quan tâm lốp xe", "type": after_type,
                    "user_id": list(after_user) if after_user else False,
                    "partner_id": False}]
        if model == "res.users" and method == "name_search":
            return users if users is not None else [[5, "Marc Demo"]]
        if model == "ir.model" and method == "search":
            return [628]
        if model == "mail.activity.type" and method == "search_read":
            return act_types if act_types is not None else [{"id": 2, "name": "Call"}]
        if model == "mail.activity" and method == "create":
            return 38
        return []

    monkeypatch.setattr(server, "odoo", fake_odoo)
    # log_activity calls get_uid() directly (not through the mocked odoo()
    # gateway) to stamp user_id — skip real XML-RPC auth, same pattern as
    # tests/test_write_tools.py::_patch_execute_raises.
    monkeypatch.setattr(server, "_uid", 1)
    return calls


# ── create_lead ──────────────────────────────────────────────────────────────

def test_create_lead_happy(monkeypatch):
    calls = _fake(monkeypatch)
    data = _env(fn("create_lead")(name="Quan tâm lốp xe",
                                  contact_name="Trần Phúc", phone="0901234567"))
    assert data["ok"] is True
    assert data["ref"] == "Quan tâm lốp xe"
    assert data["model"] == "crm.lead" and data["res_id"] == 45
    assert data["state"] == "lead"
    create = next(c for c in calls if c["method"] == "create")
    assert create["args"][0]["type"] == "lead"
    assert create["args"][0]["contact_name"] == "Trần Phúc"
    assert "name" in create["args"][0]


def test_create_lead_requires_name(monkeypatch):
    calls = _fake(monkeypatch)
    data = _env(fn("create_lead")(contact_name="Trần Phúc"))
    assert data["ok"] is False
    assert not any(c["method"] == "create" for c in calls)


def test_create_lead_odoo_error_becomes_envelope(monkeypatch):
    def boom(*a, **k):
        raise ValueError("boom")
    monkeypatch.setattr(server, "odoo", boom)
    data = _env(fn("create_lead")(name="X", contact_name="Y"))
    assert data["ok"] is False and "boom" in data["display"]


# ── convert_lead ─────────────────────────────────────────────────────────────

def test_convert_lead_not_found(monkeypatch):
    calls = _fake(monkeypatch, lead_rows=[])
    data = _env(fn("convert_lead")(lead_id=999))
    assert data["ok"] is False
    assert "không tìm thấy" in data["display"].lower()
    assert not any(c["method"] == "convert_opportunity" for c in calls)


def test_convert_lead_already_opportunity(monkeypatch):
    calls = _fake(monkeypatch, lead_rows=[_lead(type="opportunity")])
    data = _env(fn("convert_lead")(lead_id=45))
    assert data["ok"] is False
    assert "đã là cơ hội" in data["display"].lower()
    assert not any(c["method"] == "convert_opportunity" for c in calls)


def test_convert_lead_happy_no_partner_no_assignee(monkeypatch):
    calls = _fake(monkeypatch)
    data = _env(fn("convert_lead")(lead_id=45))
    assert data["ok"] is True and data["state"] == "opportunity"
    conv = next(c for c in calls if c["method"] == "convert_opportunity")
    assert conv["args"] == [[45], False]
    # no partner to restore, no assignee -> no write call at all
    assert not any(c["method"] == "write" for c in calls)


def test_convert_lead_restores_partner(monkeypatch):
    calls = _fake(monkeypatch, lead_rows=[_lead(partner_id=[15, "Azure Interior"])])
    data = _env(fn("convert_lead")(lead_id=45))
    assert data["ok"] is True
    w = next(c for c in calls if c["method"] == "write")
    assert w["args"][1] == {"partner_id": 15}
    # ordering: convert BEFORE restore-write
    idx = [c["method"] for c in calls]
    assert idx.index("convert_opportunity") < idx.index("write")


def test_convert_lead_with_assignee(monkeypatch):
    calls = _fake(monkeypatch)
    data = _env(fn("convert_lead")(lead_id=45, assignee_name="Marc Demo"))
    assert data["ok"] is True
    w = next(c for c in calls if c["method"] == "write")
    assert w["args"][1] == {"user_id": 5}


def test_convert_lead_assignee_ambiguous_fails_before_convert(monkeypatch):
    calls = _fake(monkeypatch, users=[[5, "Marc Demo"], [6, "Marc D."]])
    data = _env(fn("convert_lead")(lead_id=45, assignee_name="Marc"))
    assert data["ok"] is False
    assert "nhiều" in data["display"].lower()
    assert not any(c["method"] == "convert_opportunity" for c in calls)


def test_convert_lead_assignee_not_found_fails_before_convert(monkeypatch):
    calls = _fake(monkeypatch, users=[])
    data = _env(fn("convert_lead")(lead_id=45, assignee_name="Nguyễn Không Tồn Tại"))
    assert data["ok"] is False
    assert not any(c["method"] == "convert_opportunity" for c in calls)


# ── log_activity ─────────────────────────────────────────────────────────────

def test_log_activity_happy_uses_res_model_id(monkeypatch):
    calls = _fake(monkeypatch)
    data = _env(fn("log_activity")(lead_id=45, activity_type="Call",
                                   summary="Tư vấn thông số lốp"))
    assert data["ok"] is True
    assert data["model"] == "mail.activity" and data["res_id"] == 38
    create = next(c for c in calls if c["model"] == "mail.activity"
                  and c["method"] == "create")
    vals = create["args"][0]
    # Probe-verified shape: res_model_id (ir.model id), NOT res_model char.
    assert vals["res_model_id"] == 628
    assert "res_model" not in vals
    assert vals["res_id"] == 45
    assert vals["activity_type_id"] == 2
    assert vals["date_deadline"]          # always sent (required=True field)
    assert "user_id" in vals


def test_log_activity_type_resolved_by_name_not_hardcoded(monkeypatch):
    calls = _fake(monkeypatch, act_types=[{"id": 3, "name": "Meeting"}])
    data = _env(fn("log_activity")(lead_id=45, activity_type="Meeting",
                                   summary="Gặp khách"))
    assert data["ok"] is True
    tsearch = next(c for c in calls if c["model"] == "mail.activity.type")
    assert ["name", "=", "Meeting"] in tsearch["args"][0]
    create = next(c for c in calls if c["model"] == "mail.activity"
                  and c["method"] == "create")
    assert create["args"][0]["activity_type_id"] == 3


def test_log_activity_unknown_type_fails(monkeypatch):
    calls = _fake(monkeypatch, act_types=[])
    data = _env(fn("log_activity")(lead_id=45, activity_type="Karaoke",
                                   summary="x"))
    assert data["ok"] is False
    assert not any(c["model"] == "mail.activity" and c["method"] == "create"
                   for c in calls)


def test_log_activity_lead_not_found(monkeypatch):
    calls = _fake(monkeypatch, lead_rows=[])
    data = _env(fn("log_activity")(lead_id=999, activity_type="Call", summary="x"))
    assert data["ok"] is False
    assert not any(c["model"] == "mail.activity" for c in calls)


def test_convert_opportunity_in_method_map():
    assert server.ODOO_METHOD_OPERATION_MAP.get("convert_opportunity") == "write"
