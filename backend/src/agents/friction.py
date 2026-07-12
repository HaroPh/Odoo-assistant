# backend/src/agents/friction.py
"""Sink bền vững cho friction event của write-planner (spec 2026-07-12).

Ghi 1 dòng JSON/event vào logs/planner_friction.jsonl ở chế độ APPEND —
sống qua restart, khác backend_err.log bị start-dev.ps1 truncate mỗi lần.
Fail-open: observability không bao giờ được làm hỏng planner — mọi lỗi
ghi (disk đầy, thiếu quyền, path hỏng) bị nuốt im lặng.

Phân tích nhanh (chạy từ repo root):

  # Phân bố outcome — tỷ lệ hỏng JSON tổng thể (mẫu số = tổng số dòng)
  python -c "import json,collections;print(collections.Counter(json.loads(l)['outcome'] for l in open('logs/planner_friction.jsonl',encoding='utf-8')))"

  # Tool nào hay phải cứu/hỏng
  python -c "import json,collections;print(collections.Counter(json.loads(l).get('tool') for l in open('logs/planner_friction.jsonl',encoding='utf-8') if json.loads(l)['outcome']!='raw'))"

  # Xem các ca fail (kèm excerpt) để chẩn đoán
  python -c "import json;[print(json.dumps(json.loads(l),ensure_ascii=False,indent=1)) for l in open('logs/planner_friction.jsonl',encoding='utf-8') if json.loads(l)['outcome']=='fail']"
"""
import json
import os
from pathlib import Path

# parents[3]: agents → src → backend → repo root (pattern jobs/registry.py)
_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "logs" / "planner_friction.jsonl"


def log_friction(event: dict) -> None:
    """Append event thành 1 dòng JSON — nuốt MỌI exception (fail-open).

    Env FRICTION_LOG_PATH đọc mỗi lần gọi (không cache) để test monkeypatch
    được tự nhiên."""
    try:
        path = Path(os.environ.get("FRICTION_LOG_PATH") or _DEFAULT_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — fail-open theo spec §4
        pass
