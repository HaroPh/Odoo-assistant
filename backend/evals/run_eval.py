# backend/evals/run_eval.py
"""Runner eval M3 — đo model THẬT qua LiteLLM (không mock).

  python -m backend.evals.run_eval --set intent --model qwen3:8b --save-baseline
  python -m backend.evals.run_eval --set intent --model gemini-flash-lite \
      --baseline backend/evals/baseline-qwen3-8b-intent.json

Gate (ADR-009 M3): intent → acc(model) >= acc(baseline).
confirm → zero false-CONFIRM (kỳ vọng cancel/unclear mà đoán confirm) VÀ
acc >= baseline_acc - (1/len(cases)).
Exit 0 = đạt; 1 = trượt; 2 = lỗi hạ tầng.
Exit 2 khi có case lỗi sau retry (S2) — đo không trọn vẹn thì không gate/không lưu baseline.
"""
import argparse, asyncio, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.evals.cases import (CHITCHAT_CASES, CONFIRM_CASES,
                                 HALLUCINATION_MARKERS, INTENT_CASES)
from backend.src.agents.prompts import INTENT_ROUTER_PROMPT
from backend.src.agents.confirmation import _LLM_PROMPT
from backend.jobs.resilience import run_resilient

VALID_INTENTS = {"erp_read", "erp_write", "rag", "mixed", "unknown"}


def _llm(model: str) -> ChatOpenAI:
    return ChatOpenAI(model=model,
                      base_url=os.environ.get("LITELLM_URL", "http://localhost:4000/v1"),
                      api_key=os.environ.get("LITELLM_MASTER_KEY", ""),
                      temperature=0, timeout=60)


async def eval_intent(llm, pace: float = 0.0, checkpoint_path=None):
    async def call(case):
        text, expected = case
        resp = await llm.ainvoke([SystemMessage(content=INTENT_ROUTER_PROMPT),
                                  HumanMessage(content=text)])
        got = resp.content.strip().lower()
        got = got if got in VALID_INTENTS else "unknown"   # đúng logic node thật
        if got != expected:
            return {"text": text, "expected": expected, "got": got}
        return None
    fails, errors = await run_resilient(INTENT_CASES, call, pace=pace,
                                        checkpoint_path=checkpoint_path)
    n = len(INTENT_CASES)
    return {"set": "intent", "n": n,
            "acc": (n - len(fails) - len(errors)) / n,
            "fails": fails, "errors": errors}


async def eval_confirm(llm, pace: float = 0.0, checkpoint_path=None):
    async def call(case):
        text, expected = case
        resp = await llm.ainvoke([SystemMessage(content=_LLM_PROMPT),
                                  HumanMessage(content=text)])
        v = resp.content.strip().upper()
        got = "confirm" if "CONFIRM" in v else "cancel" if "CANCEL" in v else "unclear"
        if got != expected:
            return {"text": text, "expected": expected, "got": got}
        return None
    fails, errors = await run_resilient(CONFIRM_CASES, call, pace=pace,
                                        checkpoint_path=checkpoint_path)
    n = len(CONFIRM_CASES)
    # hướng nguy hiểm: đoán CONFIRM khi không phải. CHỈ đếm từ fails (phép đo
    # thành công) — lỗi API (errors) không bao giờ là false_confirm.
    false_confirm = sum(1 for f in fails if f["got"] == "confirm")
    return {"set": "confirm", "n": n,
            "acc": (n - len(fails) - len(errors)) / n,
            "false_confirm": false_confirm, "fails": fails, "errors": errors}


async def eval_chitchat(llm, pace: float = 0.0, checkpoint_path=None):
    """Chống bịa hành động đã xảy ra — chitchat (respond_unknown) không bind
    tool nào, nên bất kỳ khẳng định 'đã làm X' đều là bịa. Gate tuyệt đối
    (violations phải = 0), KHÔNG so baseline (không có 'câu trả lời đúng' cho
    chit-chat tự do). Gọi LLM giống hệt respond_unknown thật: KHÔNG
    SystemMessage — chỉ 1 HumanMessage, để đo đúng hành vi production."""
    async def call(text):
        resp = await llm.ainvoke([HumanMessage(content=text)])
        content_lower = resp.content.lower()
        matched = [m for m in HALLUCINATION_MARKERS if m in content_lower]
        if matched:
            return {"text": text, "response": resp.content,
                    "matched_markers": matched}
        return None
    fails, errors = await run_resilient(CHITCHAT_CASES, call, pace=pace,
                                        checkpoint_path=checkpoint_path)
    return {"set": "chitchat", "n": len(CHITCHAT_CASES),
            "violations": len(fails), "fails": fails, "errors": errors}


async def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["intent", "confirm"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--baseline")
    ap.add_argument("--pace", type=float, default=0.0,
                    help="giây giãn cách giữa 2 call (R8: cloud RPM=15 → dùng 5.0)")
    args = ap.parse_args(argv)

    try:
        result = await (eval_intent if args.set == "intent" else eval_confirm)(
            _llm(args.model), pace=args.pace)
    except Exception as e:   # noqa: BLE001 — hạ tầng (LiteLLM/key/model) hỏng
        print(f"INFRA ERROR: {e}"); sys.exit(2)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    # S2 spec §3: đo không trọn vẹn → exit 2 TRƯỚC mọi nhánh baseline —
    # baseline khuyết tật sẽ đầu độc mọi gate về sau.
    if result["errors"]:
        print(f"INFRA ERROR: {len(result['errors'])} case lỗi sau retry — "
              "không đủ điều kiện gate/baseline")
        sys.exit(2)

    here = os.path.dirname(__file__)
    if args.save_baseline:
        path = os.path.join(here, f"baseline-{args.model.replace(':','-')}-{args.set}.json")
        json.dump(result, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"baseline saved: {path}"); sys.exit(0)

    if args.baseline:
        base = json.load(open(args.baseline, encoding="utf-8"))
        if args.set == "intent":
            ok = result["acc"] >= base["acc"]
        else:
            ok = result["false_confirm"] == 0 and result["acc"] >= base["acc"] - 1 / result["n"]
        print(f"GATE {'PASS' if ok else 'FAIL'} — model={result['acc']:.3f} baseline={base['acc']:.3f}")
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
