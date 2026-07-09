# scripts/run-eval-gate-scheduled.ps1 — Task Scheduler action cho ERP-AI-EvalGate.
# Load env từ .env MỖI LẦN chạy (không bake lúc đăng ký — .env đổi là đêm sau
# đo config mới ngay). Output nối vào logs/jobs/eval-gate-scheduled.log.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Get-Content "$root\.env" | ForEach-Object {
    if ($_ -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
        [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim(), "Process")
    }
}

New-Item -ItemType Directory -Force "$root\logs\jobs" | Out-Null
$py  = "$root\.venv\Scripts\python.exe"
$log = "$root\logs\jobs\eval-gate-scheduled.log"

Set-Location $root
# PYTHONUTF8=1: bắt buộc Python dùng UTF-8 cho MỌI stream (PEP 540) — không có
# biến này, stdout redirect ra file dùng ANSI codepage (cp1252 trên máy dev),
# text tiếng Việt có dấu crash UnicodeEncodeError, mất verdict thật (whole-
# branch review finding, Critical — backend/jobs/__main__.py cũng tự
# reconfigure UTF-8, đây là lớp phòng thủ thứ 2 cho chính script này + mọi
# subprocess con nó gọi).
$env:PYTHONUTF8 = "1"
# cmd /c để redirect stderr của native exe an toàn trên PowerShell 5.1
& cmd.exe /c "echo === $(Get-Date -Format s) === >> `"$log`" && `"$py`" -m backend.jobs run eval-gate --scheduled >> `"$log`" 2>&1"
exit $LASTEXITCODE
