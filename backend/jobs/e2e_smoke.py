# backend/jobs/e2e_smoke.py
"""Job e2e-smoke — wrap live_verify_auto_chain thành job ON-DEMAND ONLY.

KHÔNG lên lịch đêm (schedulable=False, enforcement ở CLI --scheduled): mỗi lần
chạy TẠO ĐƠN NHÁP THẬT trong Odoo, và cần backend :8000 + MCP :8001 chạy host
(start-dev.ps1) — không chắc sống ban đêm. Đây là job chứng minh seam
"satellite = client của /v1" (khóa #9): script bên dưới gọi /v1/chat/completions.
"""
import socket
import subprocess
import sys
import urllib.request

from backend.jobs.registry import (GATE_FAIL, INFRA_ERROR, PASS, REPO_ROOT, Job,
                                   JobResult, register)

BACKEND_HEALTH = "http://localhost:8000/health"
MCP_PORT = 8001
SCRIPT = REPO_ROOT / "backend" / "tests" / "live_verify_auto_chain.py"
ODOO_NOTE = "tạo đơn nháp THẬT trong Odoo — dọn tay nếu cần"


def _preflight() -> str | None:
    """None = stack sẵn sàng; str = lý do không chạy được."""
    try:
        with urllib.request.urlopen(BACKEND_HEALTH, timeout=3) as r:
            if r.status != 200:
                return f"backend /health trả {r.status}"
    except OSError as e:
        return f"backend :8000 không chạy ({e}) — bật start-dev.ps1 trước"
    try:
        with socket.create_connection(("127.0.0.1", MCP_PORT), timeout=3):
            pass
    except OSError as e:
        return f"MCP :{MCP_PORT} không chạy ({e}) — bật start-dev.ps1 trước"
    return None


def run(args) -> JobResult:
    err = _preflight()
    if err:
        print(f"PREFLIGHT FAIL: {err}")
        return JobResult("e2e-smoke", INFRA_ERROR, "ERROR", {"preflight": err})
    print(f"LƯU Ý: {ODOO_NOTE}.")
    try:
        proc = subprocess.run([sys.executable, str(SCRIPT)], cwd=REPO_ROOT,
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=600)
    except subprocess.TimeoutExpired as e:
        detail = {"error": f"e2e-smoke timeout sau {e.timeout}s — script con treo",
                  "note": ODOO_NOTE}
        return JobResult("e2e-smoke", INFRA_ERROR, "ERROR", detail)
    detail = {"returncode": proc.returncode, "stdout": proc.stdout[-8000:],
              "stderr": proc.stderr[-4000:], "note": ODOO_NOTE}
    if proc.returncode == 0:
        return JobResult("e2e-smoke", PASS, "PASS", detail)
    return JobResult("e2e-smoke", GATE_FAIL, "FAIL", detail)


register(Job("e2e-smoke", run,
             "e2e smoke qua /v1 (cần full stack; tạo đơn nháp thật trong Odoo)",
             schedulable=False))
