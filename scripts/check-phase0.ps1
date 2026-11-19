# check-phase0.ps1 - one command that checks the whole Phase 0 setup on Windows.
#
# 1. Stops if .env is missing.
# 2. Builds and starts the containers (docker compose up -d --build).
# 3. Waits until GET /health answers, i.e. the API and the database are up.
# 4. Runs scripts/smoke_claude.py: one Claude call per model, traced in LangSmith.
# Usage: ./scripts/check-phase0.ps1 (needs ANTHROPIC_API_KEY and LANGSMITH_API_KEY in .env)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path .env)) {
    Write-Host "No .env found. Copy .env.example to .env and fill in the keys." -ForegroundColor Yellow
    exit 1
}

docker compose up -d --build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Waiting for the API..."
$health = $null
for ($i = 0; $i -lt 30; $i++) {
    try { $health = Invoke-RestMethod http://localhost:8000/health; break } catch { Start-Sleep 2 }
}
if (-not $health) { docker compose logs api --tail 50; exit 1 }
Write-Host "/health: $($health | ConvertTo-Json -Compress)" -ForegroundColor Green

docker compose exec api python scripts/smoke_claude.py
exit $LASTEXITCODE
