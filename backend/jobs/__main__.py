# backend/jobs/__main__.py
"""CLI Job Runner: python -m backend.jobs {list,run <job>} (Phase C skeleton).

Job đăng ký bằng side-effect import module — thêm job mới = thêm 1 dòng import
dưới đây + 1 module trong backend/jobs/.
"""
import sys
import time
from datetime import datetime

# Whole-branch review finding (Critical): khi stdout/stderr bị redirect ra file
# (đúng cách Task Scheduler chạy `>> log 2>&1`), Windows dùng ANSI codepage
# (cp1252 trên máy này) thay vì UTF-8 — text tiếng Việt có dấu (kể cả "→")
# crash UnicodeEncodeError, làm mất verdict thật và thoát exit 1 (vi phạm exit
# contract 0/1/2). Console tương tác không dính lỗi này (Python dùng Windows
# Console API, không qua codepage) — đó là lý do bug ẩn qua mọi lần test tay.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# ── job modules đăng ký tại import (Task 3/4 thêm dòng ở đây) ────────────────
from backend.jobs import eval_gate  # noqa: F401  (đăng ký side-effect)
from backend.jobs import e2e_smoke  # noqa: F401  (đăng ký side-effect)
from backend.jobs import e2e_skill_discount  # noqa: F401  (đăng ký side-effect)
from backend.jobs import e2e_skill_warehouse  # noqa: F401  (đăng ký side-effect)
from backend.jobs import e2e_skill_delivery  # noqa: F401  (đăng ký side-effect)

from backend.jobs.registry import (INFRA_ERROR, JOBS, JobResult, write_result)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m backend.jobs")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="liệt kê job đã đăng ký")

    run_p = sub.add_parser("run", help="chạy 1 job")
    run_p.add_argument("job", choices=sorted(JOBS))
    run_p.add_argument("--scheduled", action="store_true",
                       help="đặt bởi Task Scheduler — job on-demand-only từ chối flag này")

    # Chỉ gắn add_args của ĐÚNG job được chọn — không gắn tất cả job đã đăng ký
    # lên chung 1 subparser, tránh đụng tên flag giữa các job không liên quan
    # (vd 2 job cùng có --model). Pre-parse chỉ để đọc args.job, còn lại bỏ qua.
    pre, _ = ap.parse_known_args(argv)
    selected = JOBS.get(getattr(pre, "job", None))
    if selected and selected.add_args:
        selected.add_args(run_p)

    args = ap.parse_args(argv)

    if args.command == "list":
        for job in sorted(JOBS.values(), key=lambda j: j.name):
            mode = "schedulable" if job.schedulable else "on-demand only"
            print(f"{job.name:<12} [{mode:<14}] {job.description}")
        return 0

    job = JOBS[args.job]
    if args.scheduled and not job.schedulable:
        print(f"REFUSED: job '{job.name}' là on-demand only — không chạy theo lịch")
        return INFRA_ERROR

    started = datetime.now().isoformat(timespec="seconds")
    t0 = time.monotonic()
    try:
        result = job.fn(args)
    except Exception as e:  # noqa: BLE001 — job crash = lỗi hạ tầng, không nuốt im lặng
        result = JobResult(job=job.name, exit_code=INFRA_ERROR, verdict="ERROR",
                           detail={"error": str(e)})
    result.started_at = started
    result.duration_s = round(time.monotonic() - t0, 1)
    path = write_result(result)
    print(f"== {result.verdict} == exit {result.exit_code} → {path}")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
