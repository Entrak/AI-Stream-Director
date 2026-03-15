# Run script - Activates venv and starts the Stream Producer
# Usage: .\run.ps1 [--calibrate] [--debug] [--status-interval N]

param(
    [switch]$Calibrate,
    [switch]$Debug,
    [switch]$Preflight,
    [switch]$FirstRun,
    [int]$StatusInterval = 30
)

if ($FirstRun) {
    & ".\setup_venv.ps1" -FirstRun
    exit $LASTEXITCODE
}

$VENV_DIR = if (Test-Path ".venv") { ".venv" } elseif (Test-Path "venv") { "venv" } else { $null }

# Check if venv exists
if (-not $VENV_DIR) {
    Write-Host "`nERROR: Virtual environment not found!" -ForegroundColor Red
    Write-Host "Run setup first: .\setup_venv.ps1`n" -ForegroundColor Yellow
    exit 1
}

# Activate virtual environment
Write-Host "`nActivating virtual environment..." -ForegroundColor Cyan
& "$VENV_DIR\Scripts\Activate.ps1"

# Build arguments for main.py
$cliParameters = @()

if ($Calibrate) {
    $cliParameters += "--calibrate"
}

if ($Debug) {
    $cliParameters += "--debug"
}

if ($Preflight) {
    $cliParameters += "--preflight"
}

if ($StatusInterval -gt 0) {
    $cliParameters += "--status-interval"
    $cliParameters += $StatusInterval
}

# Run the application
Write-Host "Starting Twitch AI Stream Producer...`n" -ForegroundColor Green

if ($cliParameters.Count -gt 0) {
    python main.py @cliParameters
} else {
    python main.py
}

# Capture exit code
$exitCode = $LASTEXITCODE

# Deactivate venv
deactivate

# Exit with same code as application
exit $exitCode
