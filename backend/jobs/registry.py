# backend/jobs/registry.py
"""Job registry — satellite Job Runner (ADR-009 khóa #9, Phase C).

Satellite = process riêng, client của serving stack — core KHÔNG BAO GIỜ
import package này. Kết quả mỗi lần chạy đổ ra logs/jobs/<job>-<stamp>.json.
Exit contract kế thừa run_eval: 0=PASS, 1=GATE FAIL, 2=INFRA ERROR.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "logs" / "jobs"

PASS, GATE_FAIL, INFRA_ERROR = 0, 1, 2


@dataclasses.dataclass
class JobResult:
    job: str
    exit_code: int          # 0 PASS · 1 GATE FAIL · 2 INFRA ERROR
    verdict: str            # "PASS" | "FAIL" | "ERROR"
    detail: dict
    started_at: str = ""
    duration_s: float = 0.0


@dataclasses.dataclass
class Job:
    name: str
    fn: object              # (argparse.Namespace) -> JobResult
    description: str
    schedulable: bool       # False = on-demand only, --scheduled bị từ chối
    add_args: object = None # (argparse.ArgumentParser) -> None, hoặc None


JOBS: dict[str, Job] = {}


def register(job: Job) -> None:
    JOBS[job.name] = job


def write_result(result: JobResult) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    path = LOGS_DIR / f"{result.job}-{stamp}.json"
    path.write_text(
        json.dumps(dataclasses.asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8")
    return path
