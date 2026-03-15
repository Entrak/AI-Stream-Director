# Virtual Environment Setup Script for Twitch AI Stream Producer
# Run this script once to create and configure the virtual environment

param(
    [switch]$Force,
    [switch]$FirstRun
)

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "Twitch AI Stream Producer - Virtual Environment Setup" -ForegroundColor Cyan
Write-Host "============================================================`n" -ForegroundColor Cyan

$VENV_DIR = ".venv"
$LEGACY_VENV_DIR = "venv"
$ReuseExistingVenv = $false

# Check if venv already exists
if ((Test-Path $VENV_DIR) -or (Test-Path $LEGACY_VENV_DIR)) {
    if ($Force) {
        Write-Host "Removing existing virtual environment(s)..." -ForegroundColor Yellow
        if (Test-Path $VENV_DIR) {
            Remove-Item -Recurse -Force $VENV_DIR
        }
        if (Test-Path $LEGACY_VENV_DIR) {
            Remove-Item -Recurse -Force $LEGACY_VENV_DIR
        }
    } else {
        if (Test-Path $VENV_DIR) {
            Write-Host "Virtual environment already exists at: $VENV_DIR" -ForegroundColor Yellow
        }
        if (Test-Path $LEGACY_VENV_DIR) {
            Write-Host "Virtual environment already exists at: $LEGACY_VENV_DIR" -ForegroundColor Yellow
        }
        if ($FirstRun) {
            Write-Host "Continuing with first-run checks using existing environment..." -ForegroundColor Cyan
            $ReuseExistingVenv = $true
        } else {
            Write-Host "Use -Force flag to recreate it." -ForegroundColor Yellow
            Write-Host "`nTo activate existing venv, run: .\activate.ps1`n" -ForegroundColor Green
            exit 0
        }
    }
}

# Check Python version
Write-Host "Checking Python installation..." -ForegroundColor Cyan
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Found: $pythonVersion" -ForegroundColor Green
    
    # Extract version number
    if ($pythonVersion -match "Python (\d+)\.(\d+)") {
        $major = [int]$Matches[1]
        $minor = [int]$Matches[2]
        
        if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
            Write-Host "`nERROR: Python 3.10 or higher required!" -ForegroundColor Red
            Write-Host "Current version: $pythonVersion" -ForegroundColor Red
            Write-Host "Download from: https://www.python.org/downloads/" -ForegroundColor Yellow
            exit 1
        }
    }
} catch {
    Write-Host "`nERROR: Python not found in PATH!" -ForegroundColor Red
    Write-Host "Install Python 3.10+ from: https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}

# Create virtual environment
if (-not $ReuseExistingVenv) {
    Write-Host "`nCreating virtual environment..." -ForegroundColor Cyan
    python -m venv $VENV_DIR

    if ($LASTEXITCODE -ne 0) {
        Write-Host "`nERROR: Failed to create virtual environment!" -ForegroundColor Red
        exit 1
    }

    Write-Host "Virtual environment created successfully!" -ForegroundColor Green
}

# Activate virtual environment
Write-Host "`nActivating virtual environment..." -ForegroundColor Cyan
& "$VENV_DIR\Scripts\Activate.ps1"

# Upgrade pip
Write-Host "`nUpgrading pip..." -ForegroundColor Cyan
python -m pip install --upgrade pip

# Install dependencies
Write-Host "`nInstalling dependencies from requirements.txt..." -ForegroundColor Cyan
Write-Host "(This may take several minutes...)`n" -ForegroundColor Yellow

pip install -r requirements.txt

if ($LASTEXITCODE -ne 0) {
    Write-Host "`nWARNING: Some packages failed to install!" -ForegroundColor Yellow
    Write-Host "This is often due to:" -ForegroundColor Yellow
    Write-Host "  - PyAudio requires Microsoft C++ Build Tools" -ForegroundColor Yellow
    Write-Host "  - Some packages need system dependencies installed first" -ForegroundColor Yellow
    Write-Host "`nSee SETUP.md for detailed installation instructions.`n" -ForegroundColor Cyan
} else {
    if ((Test-Path ".env.example") -and (-not (Test-Path ".env"))) {
        Copy-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example" -ForegroundColor Green
    }

    Write-Host "`n============================================================" -ForegroundColor Green
    Write-Host "Setup Complete!" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "`nVirtual environment is ready at: $VENV_DIR" -ForegroundColor White
    Write-Host "`nNext steps:" -ForegroundColor Cyan
    Write-Host "  1. Activate venv: .\activate.ps1" -ForegroundColor White
    Write-Host "  2. Install system dependencies (see SETUP.md)" -ForegroundColor White
    Write-Host "  3. Fill .env with TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET" -ForegroundColor White
    Write-Host "  4. Set twitch_channel in config\user_config.json" -ForegroundColor White
    Write-Host "  5. Run preflight: .\run.ps1 -Preflight" -ForegroundColor White
    Write-Host "`nOr simply run: .\run.ps1`n" -ForegroundColor Green

    if ($FirstRun) {
        Write-Host "`nRunning first-run guided checks..." -ForegroundColor Cyan

        $ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
        if ($ollamaCmd) {
            Write-Host "Ensuring Ollama model qwen3:8b is available..." -ForegroundColor Cyan
            ollama pull qwen3:8b
        } else {
            Write-Host "Ollama not found in PATH; skipping model pull. Install Ollama first." -ForegroundColor Yellow
        }

        & ".\run.ps1" -Preflight
    }
}

# Deactivate
deactivate
