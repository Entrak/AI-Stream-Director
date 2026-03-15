param(
    [string]$DockUrl = "http://localhost:5000/obs_dock.html",
    [string]$DockName = "AI Producer",
    [switch]$LaunchOBS
)

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " AI Producer OBS Dock Quick Installer" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

try {
    Set-Clipboard -Value $DockUrl
    Write-Host "✓ Dock URL copied to clipboard:" -ForegroundColor Green
    Write-Host "  $DockUrl"
} catch {
    Write-Host "! Could not copy to clipboard automatically. URL:" -ForegroundColor Yellow
    Write-Host "  $DockUrl"
}

Write-Host ""
Write-Host "1) In OBS: View -> Docks -> Custom Browser Docks..." -ForegroundColor White
Write-Host "2) Name: $DockName" -ForegroundColor White
Write-Host "3) URL : $DockUrl" -ForegroundColor White
Write-Host "4) Click Apply" -ForegroundColor White
Write-Host ""

if ($LaunchOBS) {
    $candidates = @(
        "$env:ProgramFiles\obs-studio\bin\64bit\obs64.exe",
        "$env:ProgramFiles(x86)\obs-studio\bin\64bit\obs64.exe"
    )

    $obsPath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($obsPath) {
        Write-Host "Launching OBS..." -ForegroundColor Green
        Start-Process -FilePath $obsPath | Out-Null
    } else {
        Write-Host "OBS executable not found in default path." -ForegroundColor Yellow
        Write-Host "Launch OBS manually and paste the URL from clipboard." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Tip: Keep this dock visible while streaming for controls + safety + coaching history." -ForegroundColor DarkGray
