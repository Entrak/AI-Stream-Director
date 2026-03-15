# Twitch AI Stream Producer - Setup Guide

## System Requirements

- **OS**: Windows 10/11
- **GPU**: NVIDIA RTX 4070 (or similar with 8GB+ VRAM)
- **RAM**: 16GB minimum
- **Python**: 3.10 or higher
- **Storage**: 10GB free space (for models and temp files)

## Installation Steps

### 1. Install Python Dependencies

**Automated Setup (Recommended):**

```powershell
# Run the setup script (creates venv and installs everything)
.\setup_venv.ps1
```

This handles virtual environment creation, pip upgrade, and dependency installation automatically.

**Manual Setup:**

```powershell
# Create virtual environment (recommended)
python -m venv venv

# Activate it
.\venv\Scripts\Activate.ps1

# Upgrade pip
python -m pip install --upgrade pip

# Install requirements
pip install -r requirements.txt
```

**Troubleshooting Package Installation:**

If PyAudio fails to install:
- Install Microsoft C++ Build Tools first
- Or use pre-built wheel: `pip install pipwin && pipwin install pyaudio`

If faster-whisper fails:
- Ensure CUDA Toolkit is installed first (see step 4 below)
- May fall back to CPU mode if CUDA not detected

### 2. Install Tesseract OCR

Tesseract is required for OCR chat reading:

1. Download installer: https://github.com/UB-Mannheim/tesseract/wiki
2. Run installer (default location: `C:\Program Files\Tesseract-OCR`)
3. Add to PATH:
   ```powershell
   $env:PATH += ";C:\Program Files\Tesseract-OCR"
   [Environment]::SetEnvironmentVariable("PATH", $env:PATH, "Machine")
   ```
4. Verify installation:
   ```powershell
   tesseract --version
   ```

### 3. Install Ollama + Qwen3:8B Model

Ollama provides local AI inference:

1. Download Ollama: https://ollama.com/download/windows
2. Install and run Ollama (starts background service)
3. Download Qwen3:8B model (~5.5GB):
   ```powershell
   ollama pull qwen3:8b
   ```
4. Verify model is available:
   ```powershell
   ollama list
   ```

### 4. Setup CUDA for faster-whisper GPU Acceleration

This is **CRITICAL** for acceptable performance on RTX 4070.

#### Install CUDA Toolkit 12.x

1. Download CUDA Toolkit: https://developer.nvidia.com/cuda-downloads
2. Run installer (select custom install, uncheck GeForce Experience)
3. Verify installation:
   ```powershell
   nvcc --version
   ```

#### Install CuBLAS and cuDNN Libraries

**Important**: faster-whisper requires these libraries separately!

1. **CuBLAS**: Already included in CUDA Toolkit 12.x
2. **cuDNN**:
   - Download cuDNN from: https://developer.nvidia.com/cudnn (requires NVIDIA account)
   - Extract ZIP to CUDA installation directory (e.g., `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.3`)
   - Add cuDNN bin to PATH:
     ```powershell
     $env:PATH += ";C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.3\bin"
     ```

#### Verify GPU Acceleration

```powershell
# Run this Python script to test:
python -c "from faster_whisper import WhisperModel; model = WhisperModel('base', device='cuda'); print('GPU detected!')"
```

If you see errors about missing DLLs, cuDNN is not properly installed.

### 5. Setup Virtual Audio Cable (Optional but Recommended)

This allows mic audio capture while streaming without feedback loops:

1. Download VB-Audio Cable: https://vb-audio.com/Cable/
2. Run installer and **reboot Windows**
3. In OBS:
   - Set mic to output to VB-CABLE Input
   - In this app's config, capture from VB-CABLE Output
4. Test mic routing before first stream

### 6. Configure OBS (Dock-First)

#### Add AI Producer as OBS Dock (Recommended)

1. In OBS, open **View → Docks → Custom Browser Docks...**
2. Add a new dock named `AI Producer`
3. Set URL to: `http://localhost:5000/obs_dock.html`
4. Dock it anywhere in OBS UI (keeps guidance off-stream)

Quick setup helper:

```powershell
.\scripts\install_obs_dock.ps1 -LaunchOBS
```

#### Optional: Scene-Aware Extensive Coaching (Starting / BRB)

1. In OBS, ensure `obs-websocket` is enabled (default OBS 28+)
2. In `config/user_config.json`, set:
   - `obs_websocket_enabled: true`
   - `obs_websocket_host`, `obs_websocket_port`, `obs_websocket_password`
3. Adjust matching patterns if needed:
   - `obs_starting_scene_patterns`
   - `obs_brb_scene_patterns`
4. Keep `scene_extensive_feedback_enabled: true` to allow automatic extensive coaching
5. Optional tuning knobs:
   - `scene_guardrail_countdown_sec`
   - `scene_starting_cooldown`, `scene_brb_cooldown`
   - `starting_scene_template`, `brb_scene_template`
   - `scene_auto_disable_on_disconnect`

#### Optional: Hotkey Bridge

Send local actions to the dock control API:

```powershell
Invoke-RestMethod -Method Post -Uri http://localhost:5000/api/hotkey -ContentType "application/json" -Body '{"action":"F13"}'
```

Default mapping is configured in `hotkey_actions` in `config/user_config.json`.

#### Optional: Add TTS Audio Player as Browser Source

1. In OBS, add new **Browser Source**
2. Set URL to: `http://localhost:5000/player.html`
3. Set Width: 800, Height: 600
4. Check "Shutdown source when not visible" (unchecked)
5. Check "Refresh browser when scene becomes active" (unchecked)

#### Set Audio Monitoring (if using player source)

1. Right-click Browser Source → Filters → Audio Monitoring
2. Set to **"Monitor Only (mute output)"**
3. This ensures TTS plays in your headphones only, not on stream

#### Add Chat Overlay (OCR fallback only)

1. Add **Browser Source** with Twitch chat widget URL:
   `https://www.twitch.tv/popout/YOUR_USERNAME/chat`
2. Position in preview window where visible but not obtrusive
3. **Note the position** - you'll calibrate OCR region to this area

### 7. First Run - Calibration

**Using the run script:**

```powershell
.\run.ps1 -Calibrate
```

**Or manually:**

```powershell
# Activate venv
.\activate.ps1

# Run calibration
python main.py --calibrate
```

- Takes screenshot of your screen
- Click and drag to select the chat region in OBS preview
- Saves coordinates to `config/user_config.json`
- Run calibration again anytime with `--calibrate` flag

## Troubleshooting

### "CUDA not available" error

- Verify GPU drivers: `nvidia-smi`
- Check CUDA installation: `nvcc --version`
- Install cuDNN (most common issue)
- Restart terminal after PATH changes

### OCR not detecting chat messages

- Increase contrast in chat widget (white text on black background works best)
- Re-run calibration to ensure region is correct
- Check Tesseract installation: `tesseract --version`
- Try increasing screenshot frequency in config.py (reduce polling interval)

### Ollama connection failed

- Check Ollama service is running: `ollama list`
- Verify model downloaded: `ollama pull qwen3:8b`
- Check firewall not blocking localhost:11434

### No TTS audio in OBS

- Verify Flask server started (check console for "Running on http://localhost:5000")
- Open `http://localhost:5000/player.html` in browser to test
- Check OBS Browser Source URL matches
- Verify Audio Monitoring is set to "Monitor Only"

### Mic not capturing audio

- Check PyAudio device index (may need to adjust in voice_analyzer.py)
- If using Virtual Audio Cable, ensure routing is correct
- Test with: `python -m pyaudio` (lists available devices)

## Performance Tuning

### Reduce Latency

- Use faster-whisper `small.en` model instead of `medium.en` (edit config.py)
- Decrease chat polling interval (8s → 5s)
- Reduce voice chunk size (10s → 5s)

### Improve OCR Accuracy

- Increase chat widget font size in OBS
- Use solid background color (black recommended)
- Ensure chat widget is always visible (not overlapped by game/alerts)
- Pre-process screenshot with higher contrast (edit chat_reader.py)

### Reduce GPU VRAM Usage

- Use Whisper `small` or `tiny` model 
- Close other GPU applications (browsers, games) while running
- Monitor VRAM: `nvidia-smi`

## Next Steps

Once setup is complete:
1. Run `python main.py` to start the producer
2. Open OBS and start a test "recording" session
3. Monitor console logs for OCR output and AI feedback
4. Stream and observe TTS suggestions in real-time

For usage examples and prompt customization, see [PROMPTS.md](PROMPTS.md).
