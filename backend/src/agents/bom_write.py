# backend/src/agents/bom_write.py
"""Deterministic BoM coordinators (tier-1): create_bom + update_bom_lines.
Slot-filling qua _msg (KHÔNG interrupt); resolve sản phẩm + từng component/
change; create hiện recipe đầy đủ, update hiện DIFF hiện-tại→sau + cảnh báo
blast-radius tất định (probe 2026-07-21: MO đang mở đóng băng recipe cũ, chỉ
MO tạo sau ăn recipe mới). BoM là master data — KHÔNG NEXT_STEPS chain. Chọn
BoM lặp logic mrp_write.py có chủ đích (tránh đụng file round 4 đã merge —
xem spec §8.3). Không LLM. Xem docs/superpowers/specs/2026-07-21-bom-management-design.md."""
from langgraph.types import interrupt as _interrupt

from .state import ERPAgentState
from .tool_result import parse_write_result
from .create_order import (resolve_entity_for_order, _by_id, _ttl_expiry, _msg,
                           _disambig_q, WRITE_DISABLED_MSG)
from . import write_gate
from ..erp_query import inventory, mrp


def _finish(tool_name: str, result) -> dict:
    """mrp.bom ∉ ORDER_MODELS → không set working_context; không NEXT_STEPS
    entry → write_continuation không đề nghị bước tiếp (đúng chủ đích master-data)."""
    display, env = parse_write_result(result)
    return {**_msg(display), "pending_action": None,
            "last_write": {"tool": tool_name, **env} if env else None}


def _bom_label(b: dict) -> str:
    return b.get("code") or f"BoM #{b['id']}"


def _resolve_one(ref: str):
    """Resolve 1 tên → ('ok', {'id','name'}) | ('msg', <dict>). Gói resolve +
    disambiguation interrupt cho sản phẩm/component (pattern create_order loop)."""
    kind, val = resolve_entity_for_order(inventory.find_product(ref), ref)
    if kind == "error":
        return "msg", _msg(val)
    if kind == "none":
        return "msg", _msg(f"Không tìm thấy sản phẩm '{ref}'.")
    if kind == "ambiguous":
        chosen = _interrupt({"kind": "disambiguation",
                             "question": _disambig_q(f"sản phẩm '{ref}'", val),
                             "options": val, "expires_at": _ttl_expiry()})
        picked = _by_id(val, chosen)
        if picked is None:
            return "msg", _msg("Đã hủy.")
        return "ok", picked
    return "ok", val


def _select_bom(product_ref: str, bom_code: str):
    """Resolve sản phẩm → chọn BoM normal (code / duy nhất / disambig). Lặp
    logic mrp_write.make_create_mo_node có chủ đích (spec §8.3). →
    ('ok', bom_dict) | ('msg', <dict>)."""
    kind, product = _resolve_one(product_ref)
    if kind == "msg":
        return "msg", product
    benv = mrp.find_boms_for_variant(product["id"])
    if benv.get("status") != "success":
        return "msg", _msg(benv.get("display") or "Lỗi tra cứu định mức.")
    boms = (benv.get("data") or {}).get("boms") or []
    normal = [b for b in boms if b.get("type") == "normal"]
    if not normal:
        return "msg", _msg(f"Sản phẩm '{product['name']}' chưa có BoM (định mức) "
                           f"nào để sửa.")
    if bom_code:
        match = [b for b in normal
                 if (b.get("code") or "").casefold() == bom_code.casefold()]
        if not match:
            codes = ", ".join(_bom_label(b) for b in normal)
            return "msg", _msg(f"Không có BoM mã '{bom_code}' cho sản phẩm này. "
                               f"BoM hiện có: {codes}.")
        return "ok", match[0]
    if len(normal) == 1:
        return "ok", normal[0]
    options = [{"id": b["id"],
                "name": f"{_bom_label(b)} (cho {b['product_qty']:g} đơn vị)"}
               for b in normal]
    chosen = _interrupt({"kind": "disambiguation",
                         "question": _disambig_q("định mức (BoM)", options),
                         "options": options, "expires_at": _ttl_expiry()})
    bom = next((b for b in normal if b["id"] == chosen), None)
    if bom is None:
        return "msg", _msg("Đã hủy.")
    return "ok", bom


def make_create_bom_node(tools):
    by_name = {t.name: t for t in tools}

    async def create_bom_node(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return _msg(WRITE_DISABLED_MSG)
        args = (state.get("pending_action") or {}).get("args") or {}
        product_ref = str(args.get("product_name") or "").strip()
        raw_components = args.get("components") or []
        try:
            batch_qty = float(args.get("batch_qty") or 1)
        except (TypeError, ValueError):
            batch_qty = 1.0

        missing = []
        if not product_ref:
            missing.append("sản phẩm cần tạo định mức")
        if not raw_components:
            missing.append("danh sách nguyên liệu (kèm số lượng)")
        if missing:
            return _msg("Vui lòng cho biết: " + "; ".join(missing) + ".")
        if batch_qty <= 0:
            return _msg("Số lượng mỗi mẻ phải lớn hơn 0.")

        kind, product = _resolve_one(product_ref)
        if kind == "msg":
            return product

        components, lines = [], []
        for c in raw_components:
            ref = str(c.get("product") or "").strip()
            try:
                q = float(c.get("qty") or 0)
            except (TypeError, ValueError):
                q = 0.0
            if not ref or q <= 0:
                return _msg("Mỗi nguyên liệu cần tên và số lượng lớn hơn 0.")
            ckind, comp = _resolve_one(ref)
            if ckind == "msg":
                return comp
            if comp["id"] == product["id"]:
                return _msg("Nguyên liệu không thể là chính thành phẩm.")
            components.append({"product_id": comp["id"], "qty": q})
            lines.append(f"  - {comp['name']} × {q:g}")

        # Note BoM sẵn có (bằng chứng probe #4: multiple BoM hợp lệ).
        benv = mrp.find_boms_for_variant(product["id"])
        note = ""
        if benv.get("status") == "success":
            existing = (benv.get("data") or {}).get("boms") or []
            if existing:
                labels = ", ".join(_bom_label(b) for b in existing)
                note = (f"\nSản phẩm đã có {len(existing)} BoM ({labels}) — "
                        f"bản mới sẽ là BoM bổ sung.")

        code = str(args.get("code") or "").strip()
        head = f"Tạo BoM mới cho {product['name']}"
        if code:
            head += f" (mã {code})"
        draft = (f"{head}:\nMẻ: {batch_qty:g} đơn vị thành phẩm\n"
                 + "\n".join(lines) + note + "\nXác nhận? (có / không)")
        confirmed = _interrupt({"kind": "confirm", "question": draft,
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy tạo BoM.")

        tool = by_name.get("create_bom")
        if tool is None:
            return _msg("Công cụ tạo BoM không khả dụng.")
        try:
            result = await tool.ainvoke({"product_id": product["id"],
                                         "components": components,
                                         "batch_qty": batch_qty, "code": code})
        except Exception as e:  # noqa: BLE001
            return _msg(f"Lỗi khi tạo BoM: {e}")
        return _finish("create_bom", result)

    return create_bom_node


def make_update_bom_node(tools):
    by_name = {t.name: t for t in tools}

    async def update_bom_node(state: ERPAgentState) -> dict:
        if not write_gate.write_actions_enabled():
            return _msg(WRITE_DISABLED_MSG)
        args = (state.get("pending_action") or {}).get("args") or {}
        product_ref = str(args.get("product_name") or "").strip()
        raw_changes = args.get("changes") or []
        bom_code = str(args.get("bom_code") or "").strip()

        missing = []
        if not product_ref:
            missing.append("sản phẩm")
        if not raw_changes:
            missing.append("thay đổi cần áp dụng (thêm/bớt/đổi số lượng nguyên liệu)")
        if missing:
            return _msg("Vui lòng cho biết: " + "; ".join(missing) + ".")

        kind, bom = _select_bom(product_ref, bom_code)
        if kind == "msg":
            return bom

        renv = mrp.get_bom_recipe(bom["id"])
        if renv.get("status") != "success":
            return _msg(renv.get("display") or "Lỗi tra cứu định mức.")
        cur = (renv.get("data") or {}).get("lines") or []
        by_pid = {l["product_id"]: dict(l) for l in cur}

        # Resolve từng change + validate all-or-nothing (câu lỗi thân thiện
        # phía coordinator; MCP tool validate lại — defense in depth).
        changes, after = [], {pid: dict(l) for pid, l in by_pid.items()}
        for ch in raw_changes:
            action = str(ch.get("action") or "").strip().lower()
            ref = str(ch.get("product") or "").strip()
            if action not in ("add", "remove", "set_qty"):
                return _msg(f"Thao tác '{action}' không hợp lệ "
                            f"(chỉ add/remove/set_qty).")
            if not ref:
                return _msg("Mỗi thay đổi cần nêu tên nguyên liệu.")
            ckind, comp = _resolve_one(ref)
            if ckind == "msg":
                return comp
            pid = comp["id"]
            q = None
            if action in ("add", "set_qty"):
                try:
                    q = float(ch.get("qty") or 0)
                except (TypeError, ValueError):
                    q = 0.0
                if q <= 0:
                    return _msg(f"Số lượng cho '{comp['name']}' phải lớn hơn 0.")
            if action == "add":
                if pid in by_pid:
                    return _msg(f"'{comp['name']}' đã có trong BoM — dùng "
                                f"'đổi số lượng' thay vì thêm.")
                after[pid] = {"product_id": pid, "name": comp["name"], "qty": q}
            elif action == "set_qty":
                if pid not in by_pid:
                    names = ", ".join(l["name"] for l in cur)
                    return _msg(f"'{comp['name']}' chưa có trong BoM. Nguyên "
                                f"liệu hiện có: {names}.")
                after[pid]["qty"] = q
            else:   # remove
                if pid not in by_pid:
                    names = ", ".join(l["name"] for l in cur)
                    return _msg(f"'{comp['name']}' chưa có trong BoM. Nguyên "
                                f"liệu hiện có: {names}.")
                after.pop(pid, None)
            changes.append({"action": action, "product_id": pid, "qty": q})

        if not after:
            return _msg("BoM phải còn ít nhất 1 nguyên liệu.")

        cnt_env = mrp.open_mo_count_for_bom(bom["id"])
        warn = "\n⚠ Định mức mới chỉ áp dụng cho lệnh sản xuất tạo TỪ SAU thời điểm này"
        if cnt_env.get("status") == "success":
            n = (cnt_env.get("data") or {}).get("count", 0)
            capped = (cnt_env.get("data") or {}).get("capped", False)
            if n > 0:
                warn += (f" — {'100+' if capped else n} lệnh đang mở của BoM "
                         f"này giữ nguyên định mức cũ")
        warn += "."

        cur_txt = "\n".join(f"  - {l['name']} × {l['qty']:g}" for l in cur)
        aft_txt = "\n".join(f"  - {l['name']} × {l['qty']:g}"
                            for l in after.values())
        draft = (f"Sửa BoM {_bom_label(bom)} của {product_ref}:\n"
                 f"Hiện tại:\n{cur_txt}\nSau khi sửa:\n{aft_txt}{warn}\n"
                 f"Xác nhận? (có / không)")
        confirmed = _interrupt({"kind": "confirm", "question": draft,
                                "expires_at": _ttl_expiry()})
        if not confirmed:
            return _msg("Đã hủy sửa BoM.")

        tool = by_name.get("update_bom_lines")
        if tool is None:
            return _msg("Công cụ sửa BoM không khả dụng.")
        try:
            result = await tool.ainvoke({"bom_id": bom["id"], "changes": changes})
        except Exception as e:  # noqa: BLE001
            return _msg(f"Lỗi khi sửa BoM: {e}")
        return _finish("update_bom_lines", result)

    return update_bom_node
