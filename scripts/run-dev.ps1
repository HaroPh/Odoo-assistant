# Khởi động mcp-odoo + backend (host) cho dev.
# Yêu cầu: Docker stack đã chạy (postgres, ollama, litellm, open-webui).
#   docker compose up -d postgres ollama litellm open-webui
# Dùng:  .\scripts\run-dev.ps1
# Dừng:  đóng 2 cửa sổ PowerShell bật ra, hoặc Stop-Process theo port 8000/8001.

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$py   = Join-Path $root ".venv\Scripts\python.exe"
$envFile = Join-Path $root ".env"

function Get-EnvVal($name) {
    (Select-String -Path $envFile -Pattern "^$name=(.+)$").Matches.Groups[1].Value
}
$pgpw   = Get-EnvVal "POSTGRES_PASSWORD"
$llmkey = Get-EnvVal "LITELLM_MASTER_KEY"

# Env chung
$common = @{
    ODOO_URL          = "http://localhost:8069"
    ODOO_DB           = "odoo"
    ODOO_USERNAME     = Get-EnvVal "ODOO_USERNAME"
    ODOO_PASSWORD     = Get-EnvVal "ODOO_PASSWORD"
    DATABASE_URL      = "postgresql://admin:$pgpw@localhost:5433/ai_assistant"
    LITELLM_MASTER_KEY = $llmkey
    LITELLM_URL       = "http://localhost:4000/v1"
    MCP_ODOO_URL      = "http://localhost:8001/sse"
    PYTHONIOENCODING  = "utf-8"
}
$envSetup = ($common.GetEnumerator() | ForEach-Object { "`$env:$($_.Key)='$($_.Value)'" }) -join "; "

# 1) mcp-odoo SSE :8001
Start-Process powershell -ArgumentList "-NoExit","-Command",
    "$envSetup; cd '$root'; & '$py' 'mcp-servers\odoo\server.py'"
Write-Host "→ mcp-odoo đang khởi động (cửa sổ 1, :8001)..."

Start-Sleep 3

# 2) backend FastAPI :8000
# Dùng run.py (KHÔNG `uvicorn src.main:app`): trên Windows psycopg3 async cần
# SelectorEventLoop, mà uvicorn hardcode ProactorEventLoop cho single-process.
# run.py dựng Selector loop trước khi chạy server (xem backend/run.py).
Start-Process powershell -ArgumentList "-NoExit","-Command",
    "$envSetup; cd '$root\backend'; & '$py' run.py"
Write-Host "→ backend đang khởi động (cửa sổ 2, :8000)..."

Write-Host "`nXong. Mở http://localhost:3000 (Open WebUI) → chọn model 'erp-assistant' → chat."
