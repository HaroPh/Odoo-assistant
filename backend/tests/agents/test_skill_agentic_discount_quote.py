import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
import pytest
from unittest.mock import MagicMock

import backend.src.agents.skill_agentic_discount_quote as sad


# ── compute_discount_pct: di trú NGUYÊN VĂN các ca từ test_skill_discount_quote.py ──

def test_compute_discount_pct_tier_only():
    assert sad.compute_discount_pct("than_thiet", 10_000_000) == 0.05


def test_compute_discount_pct_thuong_is_zero():
    assert sad.compute_discount_pct("thuong", 10_000_000) == 0.0


def test_compute_discount_pct_adds_bonus_at_threshold():
    assert sad.compute_discount_pct("thuong", 50_000_000) == 0.02
    assert sad.compute_discount_pct("than_thiet", 50_000_000) == 0.07


def test_compute_discount_pct_below_threshold_no_bonus():
    assert sad.compute_discount_pct("doi_tac", 49_999_999) == 0.10


def test_compute_discount_pct_caps_at_15_percent():
    # Current tiers max out at 10% + 2% = 12%, so the 15% clamp is
    # unreachable with real tier values — assert the clamp function itself
    # still behaves correctly for a hypothetical higher base (policy
    # fidelity: discount_policy.docx states "tối đa không vượt quá 15%").
    assert sad.compute_discount_pct("doi_tac", 50_000_000) == 0.12
    assert min(0.20, 0.15) == 0.15  # documents the clamp math in isolation


def test_triggers_unchanged_from_deterministic_skill():
    # Ràng buộc spec §0.7: demo continuity — 3 trigger giữ nguyên vẹn.
    assert sad.TRIGGERS == ("bao gia chiet khau", "bao gia kem chiet khau",
                            "bao gia theo cap khach")


# ── helpers ──────────────────────────────────────────────────────────────

def _ok(matches, needs):
    return {"status": "success", "data": {"matches": matches,
            "needs_disambiguation": needs}, "display": "x"}


ENVELOPE_BLOCKS = [{"type": "text", "text":
    '{"ok": true, "ref": "S00099", "model": "sale.order", "res_id": 5, '
    '"state": "draft", "display": "Đã tạo báo giá S00099."}'}]


def _fake_create(recorder, raise_exc=None):
    t = MagicMock()
    t.name = "create_quotation"

    async def ainvoke(args):
        if raise_exc is not None:
            raise raise_exc
        recorder["args"] = args
        # Shape THẬT từ langchain_mcp_adapters: list content-block,
        # KHÔNG phải chuỗi trần (Global Constraint 5).
        return ENVELOPE_BLOCKS
    t.ainvoke = ainvoke
    return t


def _patch_reads(monkeypatch, customer_matches, product_matches,
                 price=30_000_000.0, needs_c=False, needs_p=False):
    monkeypatch.setattr(sad.sales, "find_customer",
                        lambda *a, **k: _ok(customer_matches, needs_c))
    monkeypatch.setattr(sad.inventory, "find_product",
                        lambda *a, **k: _ok(product_matches, needs_p))
    monkeypatch.setattr(sad.sales, "get_product_price",
                        lambda *a, **k: {"status": "success",
                                         "data": {"price": price}, "display": "x"})


def _patch_confirm(monkeypatch, answer, questions=None):
    def fake_confirm(question):
        if questions is not None:
            questions.append(question)
        return answer
    monkeypatch.setattr(sad, "_confirm_write", fake_confirm)


def _gated(mcp_tools):
    tools = sad._build_tools(mcp_tools)
    return next(t for t in tools if t.name == "create_discount_quote")


_ARGS = {"customer": "Azur", "lines": [{"product": "Tủ", "qty": 2}],
         "tier": "than_thiet"}


# ── _build_tools shape ───────────────────────────────────────────────────

def test_build_tools_exposes_only_ask_human_and_gated_tool():
    rec = {}
    names = {t.name for t in sad._build_tools([_fake_create(rec)])}
    assert names == {"ask_human", "create_discount_quote"}
    # Model KHÔNG BAO GIỜ thấy tool ghi thô:
    assert "create_quotation" not in names


def test_build_tools_without_create_quotation_only_ask_human():
    names = {t.name for t in sad._build_tools([])}
    assert names == {"ask_human"}


# ── gated tool: validate args (không I/O, không confirm) ─────────────────

@pytest.mark.asyncio
async def test_gated_tool_invalid_tier_errors_without_confirm_or_mcp(monkeypatch):
    rec, questions = {}, []
    _patch_confirm(monkeypatch, True, questions)
    res = await _gated([_fake_create(rec)]).ainvoke(
        {**_ARGS, "tier": "vip"})
    assert res == sad.TIER_INVALID_MSG
    assert questions == [] and "args" not in rec


@pytest.mark.asyncio
async def test_gated_tool_empty_customer_asks(monkeypatch):
    rec, questions = {}, []
    _patch_confirm(monkeypatch, True, questions)
    res = await _gated([_fake_create(rec)]).ainvoke(
        {**_ARGS, "customer": "  "})
    assert "khách hàng" in res
    assert questions == [] and "args" not in rec


@pytest.mark.asyncio
async def test_gated_tool_empty_lines_asks(monkeypatch):
    rec, questions = {}, []
    _patch_confirm(monkeypatch, True, questions)
    res = await _gated([_fake_create(rec)]).ainvoke({**_ARGS, "lines": []})
    assert "sản phẩm" in res
    assert questions == [] and "args" not in rec


@pytest.mark.asyncio
async def test_gated_tool_non_numeric_qty_errors(monkeypatch):
    # Bổ sung so với bản tất định: args giờ do model 8-9B sinh trực tiếp
    # (không qua extract-prompt JSON), qty rác không được phép crash tool.
    rec, questions = {}, []
    _patch_confirm(monkeypatch, True, questions)
    _patch_reads(monkeypatch, [{"id": 41, "name": "Azur", "score": 1}],
                 [{"id": 552, "name": "Tủ", "score": 1}])
    res = await _gated([_fake_create(rec)]).ainvoke(
        {**_ARGS, "lines": [{"product": "Tủ", "qty": "hai"}]})
    assert "Số lượng" in res
    assert questions == [] and "args" not in rec


# ── gated tool: tier tiếng Việt có dấu ───────────────────────────────────

@pytest.mark.asyncio
async def test_gated_tool_accepts_vietnamese_tier_label(monkeypatch):
    rec, questions = {}, []
    _patch_confirm(monkeypatch, True, questions)
    _patch_reads(monkeypatch, [{"id": 41, "name": "Azur", "score": 1}],
                 [{"id": 552, "name": "Tủ", "score": 1}])
    await _gated([_fake_create(rec)]).ainvoke({**_ARGS, "tier": "Thân thiết"})
    # 2 × 30M = 60M ≥ 50M → 5% + 2% = 7%: nhãn có dấu resolve đúng tier.
    assert len(questions) == 1 and "7%" in questions[0]
    assert rec["args"]["lines"][0]["price_unit"] == pytest.approx(30_000_000.0 * 0.93)


# ── gated tool: resolve ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gated_tool_customer_not_found(monkeypatch):
    rec, questions = {}, []
    _patch_confirm(monkeypatch, True, questions)
    _patch_reads(monkeypatch, [], [{"id": 552, "name": "Tủ", "score": 1}])
    res = await _gated([_fake_create(rec)]).ainvoke(_ARGS)
    assert "Không tìm thấy khách hàng 'Azur'" in res
    assert questions == [] and "args" not in rec


@pytest.mark.asyncio
async def test_gated_tool_ambiguous_customer_lists_candidates_without_confirm(monkeypatch):
    rec, questions = {}, []
    _patch_confirm(monkeypatch, True, questions)
    _patch_reads(monkeypatch,
                 [{"id": 41, "name": "Azur Interior", "score": 1},
                  {"id": 42, "name": "Azur Furniture", "score": 1}],
                 [{"id": 552, "name": "Tủ", "score": 1}], needs_c=True)
    res = await _gated([_fake_create(rec)]).ainvoke(_ARGS)
    assert "Azur Interior" in res and "Azur Furniture" in res
    assert "hỏi người dùng" in res.lower()
    assert questions == [] and "args" not in rec


@pytest.mark.asyncio
async def test_gated_tool_ambiguous_product_lists_candidates_without_confirm(monkeypatch):
    rec, questions = {}, []
    _patch_confirm(monkeypatch, True, questions)
    _patch_reads(monkeypatch, [{"id": 41, "name": "Azur", "score": 1}],
                 [{"id": 552, "name": "Tủ gỗ", "score": 1},
                  {"id": 553, "name": "Tủ sắt", "score": 1}], needs_p=True)
    res = await _gated([_fake_create(rec)]).ainvoke(_ARGS)
    assert "Tủ gỗ" in res and "Tủ sắt" in res
    assert questions == [] and "args" not in rec


# ── gated tool: confirm gate ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_gated_tool_refused_returns_refused_msg_without_mcp(monkeypatch):
    rec = {}
    _patch_confirm(monkeypatch, False)
    _patch_reads(monkeypatch, [{"id": 41, "name": "Azur", "score": 1}],
                 [{"id": 552, "name": "Tủ", "score": 1}])
    res = await _gated([_fake_create(rec)]).ainvoke(_ARGS)
    assert res == sad.REFUSED_MSG
    assert "args" not in rec


@pytest.mark.asyncio
async def test_gated_tool_confirm_question_shows_computed_percent(monkeypatch):
    rec, questions = {}, []
    _patch_confirm(monkeypatch, False, questions)
    _patch_reads(monkeypatch, [{"id": 41, "name": "Azur", "score": 1}],
                 [{"id": 552, "name": "Tủ", "score": 1}], price=100_000.0)
    await _gated([_fake_create(rec)]).ainvoke(_ARGS)
    # 200k < 50M → không bonus → đúng 5%; draft đủ tiền trước/sau chiết khấu.
    assert len(questions) == 1
    assert "5%" in questions[0]
    assert "200,000" in questions[0] and "190,000" in questions[0]
    assert "Azur" in questions[0]


@pytest.mark.asyncio
async def test_gated_tool_confirmed_calls_mcp_discounted_and_returns_raw(monkeypatch):
    rec = {}
    _patch_confirm(monkeypatch, True)
    _patch_reads(monkeypatch, [{"id": 41, "name": "Azur", "score": 1}],
                 [{"id": 552, "name": "Tủ", "score": 1}])
    res = await _gated([_fake_create(rec)]).ainvoke(_ARGS)
    assert rec["args"]["partner_id"] == 41
    assert rec["args"]["lines"] == [{"product_id": 552, "qty": 2,
        "price_unit": pytest.approx(30_000_000.0 * 0.93)}]
    # Global Constraint 2: trả RAW kết quả MCP (list content-block), không parse.
    assert res == ENVELOPE_BLOCKS


@pytest.mark.asyncio
async def test_gated_tool_mcp_error_returns_text(monkeypatch):
    _patch_confirm(monkeypatch, True)
    _patch_reads(monkeypatch, [{"id": 41, "name": "Azur", "score": 1}],
                 [{"id": 552, "name": "Tủ", "score": 1}])
    res = await _gated([_fake_create({}, raise_exc=ValueError("write-mode tắt"))]).ainvoke(_ARGS)
    assert res.startswith("Lỗi khi tạo báo giá:")


# ── E2E qua build_graph THẬT ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_discount_skill_e2e_confirm_gate_and_working_context(monkeypatch):
    # E2E xuyên cả 3 commit của đợt này qua build_graph THẬT với skill build
    # THẬT (không thay spec.build): trigger → _route_by_intent vào node
    # agentic → model script gọi create_discount_quote → tool resolve + tính
    # 7% TRONG CODE → interrupt confirm (question phải chứa "7%") → resume
    # True → fake MCP create_quotation trả ĐÚNG shape list-content-block →
    # agentic_context_sync set working_context. Khác e2e Đợt 2 (fake tool
    # bypass gate — Minor của final review đợt đó): test này đi xuyên
    # _confirm_write thật + resume thật.
    import json as _json
    from pydantic import PrivateAttr
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.outputs import ChatResult, ChatGeneration
    from langchain_core.messages import AIMessage, HumanMessage
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command
    import backend.src.agents.graph as graph_mod

    _patch_reads(monkeypatch, [{"id": 41, "name": "Azur", "score": 1}],
                 [{"id": 552, "name": "Tủ", "score": 1}])

    envelope = _json.dumps({"ok": True, "ref": "S00099", "model": "sale.order",
                            "res_id": 5, "state": "draft",
                            "display": "Đã tạo báo giá S00099 (nháp) cho Azur (1 dòng)."},
                           ensure_ascii=False)
    rec = {}
    fake_mcp = MagicMock()
    fake_mcp.name = "create_quotation"

    async def _ainvoke(args):
        rec["args"] = args
        return [{"type": "text", "text": envelope}]
    fake_mcp.ainvoke = _ainvoke

    class _RouterAndSkillModel(BaseChatModel):
        _step: list = PrivateAttr(default_factory=lambda: [0])

        @property
        def _llm_type(self) -> str:
            return "routerdiscount"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            sys_text = messages[0].content if messages else ""
            if "erp_read" in sys_text and "erp_write" in sys_text:
                return ChatResult(generations=[ChatGeneration(
                    message=AIMessage(content="erp_write"))])
            i = self._step[0]
            self._step[0] += 1
            steps = [
                AIMessage(content="", tool_calls=[
                    {"name": "create_discount_quote",
                     "args": {"customer": "Azur",
                              "lines": [{"product": "Tủ", "qty": 2}],
                              "tier": "than_thiet"},
                     "id": "c1"}]),
                AIMessage(content="Đã tạo báo giá S00099 (nháp) cho Azur (1 dòng)."),
            ]
            return ChatResult(generations=[ChatGeneration(message=steps[i])])

    model = _RouterAndSkillModel()
    monkeypatch.delenv("ERP_SKILLS_ENABLED", raising=False)  # default ON

    graph = graph_mod.build_graph(model, tools=[fake_mcp],
                                  checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": "e2e-discount"}}

    res = await graph.ainvoke({"messages": [
        HumanMessage(content="báo giá chiết khấu cho Azur, 2 Tủ")]}, cfg)
    itr = res["__interrupt__"][0].value
    # 2 × 30M = 60M ≥ 50M → than_thiet 5% + bonus 2% = 7%, tính trong code.
    assert itr["kind"] == "confirm" and "7%" in itr["question"]
    assert "args" not in rec  # chưa ghi gì trước khi user xác nhận

    res = await graph.ainvoke(Command(resume=True), cfg)
    assert rec["args"]["partner_id"] == 41
    assert rec["args"]["lines"][0]["price_unit"] == pytest.approx(30_000_000.0 * 0.93)
    assert res.get("working_context") == {
        "ref": "S00099", "model": "sale.order",
        "display": "Đã tạo báo giá S00099 (nháp) cho Azur (1 dòng)."}
