# backend/jobs/eval_gate.py
"""Job eval-gate — tự động hóa cổng M3 (ADR-009 khóa #10; đóng R5/F10).

Mặc định đo config ĐANG SỐNG: intent-set trên model_for("router"), confirm-set
trên model_for("evaluator") — đúng env resolution backend dùng, nên lịch đêm trả
lời đúng câu hỏi "config production hiện tại còn khỏe so với baseline không".
--model đo candidate trước khi flip; --set đo riêng 1 set khi xét flip 1 role
(bài học Task 6 Phase A: candidate pass intent nhưng fail confirm → flip router-only).
Pacing auto (R8): cloud 5s/call (RPM=15), local 0.
S2: mỗi case retry bounded (resilience.py); case lỗi sau retry → INFRA_ERROR
(đo không trọn vẹn), circuit-breaker dừng sớm khi lỗi hệ thống.
"""
import asyncio
import json
from pathlib import Path

from backend.evals import run_eval
from backend.jobs import registry
from backend.jobs.registry import (GATE_FAIL, INFRA_ERROR, PASS, Job, JobResult,
                                   register)
from backend.src.agents.models import is_qwen, model_for

EVALS_DIR = Path(run_eval.__file__).resolve().parent
CLOUD_PACE_S = 5.0   # ~12 call/phút — an toàn dưới RPM=15 (R8)

BASELINES = {
    "intent": EVALS_DIR / "baseline-qwen3-8b-intent.json",
    "confirm": EVALS_DIR / "baseline-qwen3-8b-confirm.json",
    # "chitchat": KHÔNG có entry — gate tuyệt đối (violations==0), không
    # baseline-relative (không có "câu trả lời đúng" cho chit-chat tự do).
}
ROLE_FOR_SET = {"intent": "router", "confirm": "evaluator", "chitchat": "chitchat"}
EVAL_FN = {"intent": run_eval.eval_intent, "confirm": run_eval.eval_confirm,
           "chitchat": run_eval.eval_chitchat}


def _auto_pace(model: str) -> float:
    return 0.0 if is_qwen(model) else CLOUD_PACE_S


def _gate(set_name: str, result: dict, base: dict | None) -> bool:
    # công thức GIỮ NGUYÊN VĂN run_eval / ADR-009 M3 cho intent/confirm.
    # chitchat: gate tuyệt đối, không baseline-relative.
    if set_name == "chitchat":
        return result["violations"] == 0
    if set_name == "intent":
        return result["acc"] >= base["acc"]
    return (result["false_confirm"] == 0
            and result["acc"] >= base["acc"] - 1 / result["n"])


def run(args) -> JobResult:
    sets = ["intent", "confirm"] if args.set == "both" else [args.set]
    detail, any_fail = {}, False
    for set_name in sets:
        model = args.model if args.model is not None else model_for(ROLE_FOR_SET[set_name])
        pace = args.pace if args.pace is not None else _auto_pace(model)
        try:
            # Đọc baseline TRƯỚC khi chạy eval thật (tốn call LLM, có pacing 5s/
            # call với cloud) — baseline thiếu/hỏng thì fail nhanh, không đốt
            # call vô ích. chitchat KHÔNG có baseline (base ở lại None).
            base = None
            if set_name in BASELINES:
                base = json.loads(BASELINES[set_name].read_text(encoding="utf-8"))
            checkpoint = registry.LOGS_DIR / f"_checkpoint-eval-gate-{set_name}.json"
            result = asyncio.run(EVAL_FN[set_name](
                run_eval._llm(model), pace=pace, checkpoint_path=checkpoint))
        except Exception as e:  # noqa: BLE001 — hạ tầng (LiteLLM/key/model/baseline hỏng)
            detail[set_name] = {"model": model, "error": str(e)}
            return JobResult("eval-gate", INFRA_ERROR, "ERROR", detail)
        # S2 spec §3: có case lỗi sau retry = đo không trọn vẹn → INFRA_ERROR,
        # không có quyền PASS/FAIL (exit 1 phải luôn nghĩa "model kém").
        # CircuitBreakerOpen thì nổi từ asyncio.run vào except ở trên.
        if result.get("errors"):
            detail[set_name] = {"model": model, "pace": pace,
                                "errors": result["errors"],
                                "fails": result["fails"]}
            print(f"[{set_name}] model={model} INFRA ERROR: "
                  f"{len(result['errors'])} case lỗi sau retry — đo không trọn vẹn")
            return JobResult("eval-gate", INFRA_ERROR, "ERROR", detail)
        ok = _gate(set_name, result, base)
        any_fail |= not ok
        entry = {"model": model, "pace": pace, "gate": "PASS" if ok else "FAIL",
                 "fails": result["fails"]}
        if base is not None:
            entry.update(acc=result["acc"], baseline_acc=base["acc"],
                         false_confirm=result.get("false_confirm"))
            print(f"[{set_name}] model={model} pace={pace}s acc={result['acc']:.3f} "
                  f"baseline={base['acc']:.3f} → {'PASS' if ok else 'FAIL'}")
        else:
            entry["violations"] = result["violations"]
            print(f"[{set_name}] model={model} pace={pace}s "
                  f"violations={result['violations']} → {'PASS' if ok else 'FAIL'}")
        detail[set_name] = entry
    verdict = "FAIL" if any_fail else "PASS"
    return JobResult("eval-gate", GATE_FAIL if any_fail else PASS, verdict, detail)


def add_args(p):
    p.add_argument("--model", default=None,
                   help="candidate model (mặc định: config đang sống qua model_for)")
    p.add_argument("--set", choices=["both", "intent", "confirm", "chitchat"],
                   default="both")
    p.add_argument("--pace", type=float, default=None,
                   help="giây/call (mặc định auto: cloud 5.0, local 0)")


register(Job("eval-gate", run,
             "M3 gate: intent+confirm vs baseline + chitchat anti-hallucination "
             "(mặc định đo config sống)",
             schedulable=True, add_args=add_args))
