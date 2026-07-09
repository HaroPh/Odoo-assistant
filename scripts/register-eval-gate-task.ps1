# scripts/register-eval-gate-task.ps1 — đăng ký/gỡ lịch đêm cho eval-gate (Phase C).
# Usage:
#   .\scripts\register-eval-gate-task.ps1              # đăng ký (idempotent, daily 23:00)
#   .\scripts\register-eval-gate-task.ps1 -Unregister  # gỡ
# Lưu ý: task chạy dưới user hiện tại, chỉ khi user đang logged on (máy bật +
# logged off = không chạy — chấp nhận cho máy dev; drift-check không chạy bù).
param([switch]$Unregister)

$ErrorActionPreference = "Stop"
$TaskName = "ERP-AI-EvalGate"
$root = Split-Path -Parent $PSScriptRoot

if ($Unregister) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Đã gỡ task '$TaskName'."
    } else {
        Write-Host "Task '$TaskName' không tồn tại — không có gì để gỡ."
    }
    exit 0
}

if (-not (Test-Path "$root\.env")) { throw "Không thấy $root\.env — cần cho eval-gate." }
if (-not (Test-Path "$root\.venv\Scripts\python.exe")) { throw "Không thấy venv python." }

$runner = "$root\scripts\run-eval-gate-scheduled.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`""
$trigger = New-ScheduledTaskTrigger -Daily -At 23:00
# StartWhenAvailable=false (mặc định): máy tắt 23:00 → skip, KHÔNG chạy bù
$settings = New-ScheduledTaskSettingsSet

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Force | Out-Null
Write-Host "Đã đăng ký task '$TaskName' (daily 23:00, action: run-eval-gate-scheduled.ps1)."
Write-Host "Gỡ: .\scripts\register-eval-gate-task.ps1 -Unregister"
