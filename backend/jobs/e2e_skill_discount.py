# backend/jobs/e2e_skill_discount.py
"""Job e2e-skill-discount — wrap live_verify_skill_discount thành job ON-DEMAND
ONLY, mirror y hệt e2e_smoke.py (schedulable=False: mỗi lần chạy tạo draft
quotation THẬT trong Odoo, cần backend :8000 + MCP :8001 chạy host)."""
import json
import re
import socket
import subprocess
import sys
import urllib.request

from backend.jobs.registry import (GATE_FAIL, INFRA_ERROR, PASS, REPO_ROOT, Job,
                                   JobResult, register)

BACKEND_HEALTH = "http://localhost:8000/health"
MCP_PORT = 8001
SCRIPT = REPO_ROOT / "backend" / "tests" / "live_verify_skill_discount.py"
ODOO_NOTE = "tạo draft quotation THẬT trong Odoo (discount_quote) — dọn tay nếu cần"


def _preflight() -> str | None:
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


def _extract_result_json(stdout: str) -> dict | None:
    m = re.search(r"=== RESULT_JSON ===\n(.+?)\n=== END_RESULT_JSON ===",
                  stdout, re.DOTALL)
    return json.loads(m.group(1)) if m else None


def run(args) -> JobResult:
    err = _preflight()
    if err:
        print(f"PREFLIGHT FAIL: {err}")
        return JobResult("e2e-skill-discount", INFRA_ERROR, "ERROR", {"preflight": err})
    print(f"LƯU Ý: {ODOO_NOTE}.")
    try:
        proc = subprocess.run([sys.executable, str(SCRIPT)], cwd=REPO_ROOT,
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=600)
    except subprocess.TimeoutExpired as e:
        detail = {"error": f"timeout sau {e.timeout}s — script con treo", "note": ODOO_NOTE}
        return JobResult("e2e-skill-discount", INFRA_ERROR, "ERROR", detail)

    result_json = _extract_result_json(proc.stdout)
    detail = {"returncode": proc.returncode, "note": ODOO_NOTE,
             "raw_stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:]}
    if result_json is None:
        detail["error"] = "không parse được RESULT_JSON từ stdout script con"
        return JobResult("e2e-skill-discount", INFRA_ERROR, "ERROR", detail)
    detail["result"] = result_json
    if result_json["passed"] == result_json["n"]:
        return JobResult("e2e-skill-discount", PASS, "PASS", detail)
    return JobResult("e2e-skill-discount", GATE_FAIL, "FAIL", detail)


register(Job("e2e-skill-discount", run,
             "E2E skill agentic: discount_quote (3 kịch bản, cần full stack + write thật)",
             schedulable=False))
