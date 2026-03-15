# Quick activation script for virtual environment
# Usage: .\activate.ps1

$VENV_DIR = if (Test-Path ".venv") { ".venv" } elseif (Test-Path "venv") { "venv" } else { $null }

if (-not $VENV_DIR) {
    Write-Host "`nERROR: Virtual environment not found!" -ForegroundColor Red
    Write-Host "Run setup first: .\setup_venv.ps1`n" -ForegroundColor Yellow
    exit 1
}

Write-Host "`nActivating virtual environment..." -ForegroundColor Cyan
& "$VENV_DIR\Scripts\Activate.ps1"

Write-Host "Virtual environment activated!`n" -ForegroundColor Green
Write-Host "Python: " -NoNewline
python --version
Write-Host "Location: $VENV_DIR`n" -ForegroundColor Gray

Write-Host "Ready to run:" -ForegroundColor Cyan
Write-Host "  python main.py --calibrate   # First-time setup" -ForegroundColor White
Write-Host "  python main.py               # Start producer" -ForegroundColor White
Write-Host "  deactivate                   # Exit venv`n" -ForegroundColor White
