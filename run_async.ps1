# Run Async Stream Producer (Phase 2a Event-Driven Architecture)
# Usage: .\run_async.ps1 [-StatusInterval <seconds>]

param(
    [int]$StatusInterval = 30
)

$VENV_DIR = if (Test-Path ".venv") { ".venv" } elseif (Test-Path "venv") { "venv" } else { $null }

if (-not $VENV_DIR) {
    Write-Host "`nERROR: Virtual environment not found!" -ForegroundColor Red
    Write-Host "Run setup first: .\setup_venv.ps1`n" -ForegroundColor Yellow
    exit 1
}

Write-Host "`nActivating virtual environment..." -ForegroundColor Cyan
& "$VENV_DIR\Scripts\Activate.ps1"

Write-Host "Starting Async Stream Producer...`n" -ForegroundColor Cyan

python main_async.py --status-interval $StatusInterval

deactivate
