# backend/evals/run_eval.py
"""Runner eval M3 — đo model THẬT qua LiteLLM (không mock).

  python -m backend.evals.run_eval --set intent --model qwen3:8b --save-baseline
  python -m backend.evals.run_eval --set intent --model gemini-flash-lite \
      --baseline backend/evals/baseline-qwen3-8b-intent.json

Gate (ADR-009 M3): intent → acc(model) >= acc(baseline).
confirm → zero false-CONFIRM (kỳ vọng cancel/unclear mà đoán confirm) VÀ
acc >= baseline_acc - (1/len(cases)).
Exit 0 = đạt; 1 = trượt; 2 = lỗi hạ tầng.
"""
import argparse, asyncio, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from backend.evals.cases import INTENT_CASES, CONFIRM_CASES
from backend.src.agents.prompts import INTENT_ROUTER_PROMPT
from backend.src.agents.confirmation import _LLM_PROMPT

VALID_INTENTS = {"erp_read", "erp_write", "rag", "mixed", "unknown"}


def _llm(model: str) -> ChatOpenAI:
    return ChatOpenAI(model=model,
                      base_url=os.environ.get("LITELLM_URL", "http://localhost:4000/v1"),
                      api_key=os.environ.get("LITELLM_MASTER_KEY", ""),
                      temperature=0, timeout=60)


async def eval_intent(llm):
    fails, n = [], len(INTENT_CASES)
    for text, expected in INTENT_CASES:
        resp = await llm.ainvoke([SystemMessage(content=INTENT_ROUTER_PROMPT),
                                  HumanMessage(content=text)])
        got = resp.content.strip().lower()
        got = got if got in VALID_INTENTS else "unknown"   # đúng logic node thật
        if got != expected:
            fails.append({"text": text, "expected": expected, "got": got})
    return {"set": "intent", "n": n, "acc": (n - len(fails)) / n, "fails": fails}


async def eval_confirm(llm):
    fails, false_confirm, n = [], 0, len(CONFIRM_CASES)
    for text, expected in CONFIRM_CASES:
        resp = await llm.ainvoke([SystemMessage(content=_LLM_PROMPT),
                                  HumanMessage(content=text)])
        v = resp.content.strip().upper()
        got = "confirm" if "CONFIRM" in v else "cancel" if "CANCEL" in v else "unclear"
        if got != expected:
            fails.append({"text": text, "expected": expected, "got": got})
            if got == "confirm":
                false_confirm += 1     # hướng nguy hiểm: đoán CONFIRM khi không phải
    return {"set": "confirm", "n": n, "acc": (n - len(fails)) / n,
            "false_confirm": false_confirm, "fails": fails}


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", choices=["intent", "confirm"], required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--save-baseline", action="store_true")
    ap.add_argument("--baseline")
    args = ap.parse_args()

    try:
        result = await (eval_intent if args.set == "intent" else eval_confirm)(_llm(args.model))
    except Exception as e:   # noqa: BLE001 — hạ tầng (LiteLLM/key/model) hỏng
        print(f"INFRA ERROR: {e}"); sys.exit(2)

    print(json.dumps(result, ensure_ascii=False, indent=2))

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
