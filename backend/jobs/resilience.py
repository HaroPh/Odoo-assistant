# backend/jobs/resilience.py
"""Chuẩn resilience S2 (ADR-009 §2.2, bài học F15/C5 ChangAI): bounded retry
+ checkpoint + circuit-breaker — 1 helper dùng chung cho vòng lặp per-item.

Ngữ nghĩa lỗi (spec §3): item lỗi sau khi hết retry vào danh sách `errors`
riêng — KHÔNG gộp vào `fails`. Fails = phép đo THÀNH CÔNG cho kết quả xấu
(model trả lời sai); errors = KHÔNG ĐO ĐƯỢC. Caller quyết định errors ảnh
hưởng verdict thế nào (eval: errors ≠ rỗng → INFRA_ERROR, bảo toàn exit
contract 1=model-kém / 2=không-đo-được).

Checkpoint là BẰNG CHỨNG, không phải resume: file còn trên đĩa = lần chạy
trước chết giữa chừng (crash/breaker); run hoàn tất thì xóa. KHÔNG thêm cơ
chế đọc-checkpoint-để-tiếp-tục — eval phải là 1 lần chạy nhất quán trên
1 config model.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path


class CircuitBreakerOpen(RuntimeError):
    """Quá breaker_threshold item lỗi LIÊN TIẾP (mỗi item đã hết retry riêng)
    — lỗi mang tính hệ thống (key chết, LiteLLM sập), dừng ngay để không đốt
    quota/thời gian retry vô ích trên các item còn lại."""


def _write_checkpoint(path: Path, total: int, done: int,
                      fails: int, errors: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "total": total, "done": done, "fails": fails, "errors": errors,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }, ensure_ascii=False, indent=2), encoding="utf-8")


async def run_resilient(items, call_fn, *, pace: float = 0.0,
                        max_retries: int = 2,
                        retry_delay: float | None = None,
                        checkpoint_every: int = 10,
                        checkpoint_path: Path | None = None,
                        breaker_threshold: int = 5,
                        ) -> tuple[list[dict], list[dict]]:
    """Chạy call_fn trên từng item với retry / checkpoint / circuit-breaker.

    call_fn: async (item) -> dict | None — None = pass, dict = fail-record
    (domain tự định nghĩa shape). Items phải JSON-serializable (đi vào
    error-record và JobResult). Trả (fails, errors); error-record:
    {"item": item, "error": str(e), "attempts": tổng_attempt}.
    """
    if retry_delay is None:
        retry_delay = pace if pace > 0 else 2.0
    fails: list[dict] = []
    errors: list[dict] = []
    consecutive = 0
    total = len(items)
    for i, item in enumerate(items):
        if i and pace:
            await asyncio.sleep(pace)   # R8: giãn cách giữa 2 ITEM (cloud RPM=15)
        for attempt in range(1, max_retries + 2):
            try:
                record = await call_fn(item)
            except Exception as e:  # noqa: BLE001 — retry mọi exception (spec §2)
                if attempt <= max_retries:
                    await asyncio.sleep(retry_delay)
                    continue
                errors.append({"item": item, "error": str(e),
                               "attempts": attempt})
                consecutive += 1
                if consecutive >= breaker_threshold:
                    if checkpoint_path is not None:
                        _write_checkpoint(checkpoint_path, total, i + 1,
                                          len(fails), len(errors))
                    raise CircuitBreakerOpen(
                        f"{breaker_threshold} item lỗi liên tiếp sau retry "
                        f"(done {i + 1}/{total}, lỗi cuối: {e})") from e
                break
            if record is not None:
                fails.append(record)
            consecutive = 0   # đo được (kể cả fail-record) = hạ tầng sống
            break
        if checkpoint_path is not None and (i + 1) % checkpoint_every == 0:
            _write_checkpoint(checkpoint_path, total, i + 1,
                              len(fails), len(errors))
    if checkpoint_path is not None:
        checkpoint_path.unlink(missing_ok=True)   # run sạch: xóa bằng chứng
    return fails, errors
