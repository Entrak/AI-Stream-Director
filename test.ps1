# Test individual modules in virtual environment
# Usage: .\test.ps1 [module_name]
# Modules: smoke, chat, voice, ai, tts, config, wizard

param(
    [string]$Module = "all"
)

$VENV_DIR = if (Test-Path ".venv") { ".venv" } elseif (Test-Path "venv") { "venv" } else { $null }

if (-not $VENV_DIR) {
    Write-Host "`nERROR: Virtual environment not found!" -ForegroundColor Red
    Write-Host "Run setup first: .\setup_venv.ps1`n" -ForegroundColor Yellow
    exit 1
}

# Activate venv
Write-Host "`nActivating virtual environment..." -ForegroundColor Cyan
& "$VENV_DIR\Scripts\Activate.ps1"

Write-Host "`n============================================================" -ForegroundColor Cyan
Write-Host "Testing Twitch AI Stream Producer Modules" -ForegroundColor Cyan
Write-Host "============================================================`n" -ForegroundColor Cyan

function Test-Module {
    param($Name, $Script)
    
    Write-Host "`nTesting: $Name" -ForegroundColor Yellow
    Write-Host "============================================================" -ForegroundColor Gray
    
    python $Script
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n✓ $Name test passed" -ForegroundColor Green
        return $true
    } else {
        Write-Host "`n✗ $Name test failed (exit code: $LASTEXITCODE)" -ForegroundColor Red
        return $false
    }
}

$results = @{}

switch ($Module.ToLower()) {
    "smoke" {
        $results["Smoke"] = Test-Module "Environment Smoke" "scripts\test_env_smoke.py"
    }
    "unit" {
        Write-Host "`nRunning Unit Tests (pytest)" -ForegroundColor Yellow
        Write-Host "============================================================" -ForegroundColor Gray
        python -m pytest tests/unit/ -v --tb=short
        if ($LASTEXITCODE -eq 0) {
            Write-Host "`n✓ Unit tests passed" -ForegroundColor Green
            $results["Unit Tests"] = $true
        } else {
            Write-Host "`n✗ Unit tests failed" -ForegroundColor Red
            $results["Unit Tests"] = $false
        }
    }
    "config" {
        $results["Config"] = Test-Module "Config System" "config\config.py"
    }
    "wizard" {
        $results["Setup Wizard"] = Test-Module "Setup Wizard" "modules\setup_wizard.py"
    }
    "chat" {
        Write-Host "`nWARNING: Chat reader test requires calibrated config" -ForegroundColor Yellow
        $results["Chat Reader"] = Test-Module "Chat Reader" "modules\chat_reader.py"
    }
    "voice" {
        Write-Host "`nWARNING: Voice analyzer test requires microphone access" -ForegroundColor Yellow
        $results["Voice Analyzer"] = Test-Module "Voice Analyzer" "modules\voice_analyzer.py"
    }
    "ai" {
        Write-Host "`nWARNING: AI Producer test requires Ollama running" -ForegroundColor Yellow
        $results["AI Producer"] = Test-Module "AI Producer" "modules\ai_producer.py"
    }
    "tts" {
        $results["TTS Server"] = Test-Module "TTS Server" "modules\tts_server.py"
    }
    "all" {
        Write-Host "Running all module tests...`n" -ForegroundColor Cyan
        
        $results["Smoke"] = Test-Module "Environment Smoke" "scripts\test_env_smoke.py"
        $results["Config"] = Test-Module "Config System" "config\config.py"
        # Skip interactive tests in "all" mode
        
        Write-Host "`n`nTest Summary:" -ForegroundColor Cyan
        Write-Host "============================================================" -ForegroundColor Gray
        foreach ($test in $results.GetEnumerator()) {
            $status = if ($test.Value) { "✓ PASS" } else { "✗ FAIL" }
            $color = if ($test.Value) { "Green" } else { "Red" }
            Write-Host "$($test.Key): " -NoNewline
            Write-Host $status -ForegroundColor $color
        }
    }
    default {
        Write-Host "Unknown module: $Module" -ForegroundColor Red
        Write-Host "`nAvailable modules:" -ForegroundColor Yellow
        Write-Host "  config  - Configuration system" -ForegroundColor White
        Write-Host "  smoke   - Non-interactive environment smoke checks" -ForegroundColor White
        Write-Host "  unit    - Unit tests (pytest)" -ForegroundColor White
        Write-Host "  wizard  - Setup calibration wizard" -ForegroundColor White
        Write-Host "  chat    - Chat reader (requires calibration)" -ForegroundColor White
        Write-Host "  voice   - Voice analyzer (requires mic)" -ForegroundColor White
        Write-Host "  ai      - AI producer (requires Ollama)" -ForegroundColor White
        Write-Host "  tts     - TTS server" -ForegroundColor White
        Write-Host "  all     - Run all basic tests`n" -ForegroundColor White
    }
}

Write-Host "`n============================================================`n" -ForegroundColor Cyan

deactivate
