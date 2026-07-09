# backend/jobs/__main__.py
"""CLI Job Runner: python -m backend.jobs {list,run <job>} (Phase C skeleton).

Job đăng ký bằng side-effect import module — thêm job mới = thêm 1 dòng import
dưới đây + 1 module trong backend/jobs/.
"""
import sys
import time
from datetime import datetime

# ── job modules đăng ký tại import (Task 3/4 thêm dòng ở đây) ────────────────

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
    for job in JOBS.values():
        if job.add_args:
            job.add_args(run_p)

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
