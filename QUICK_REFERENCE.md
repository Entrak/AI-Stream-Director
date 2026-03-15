# Quick Reference - Twitch AI Stream Producer

## 🎯 Most Common Commands

```powershell
# First time setup (run once)
.\setup_venv.ps1

# First time setup + model + auth preflight (recommended)
.\run.ps1 -FirstRun

# Daily usage (run the app)
.\run.ps1

# First-time full check + Twitch authorization prompt
.\run.ps1 -Preflight

# Calibrate chat region (OCR fallback mode only)  
.\run.ps1 -Calibrate

# Debug mode
.\run.ps1 -Debug
```

## 📋 File Structure Quick Guide

| File | Purpose | When to Use |
|------|---------|-------------|
| `setup_venv.ps1` | Create virtual environment | First time only, or with `-Force` to recreate |
| `run.ps1` | Run the application | Every time you want to start the producer |
| `activate.ps1` | Activate venv manually | When you want to run Python commands directly |
| `test.ps1` | Test individual modules | Troubleshooting or verifying installation |

## 🔄 Typical Workflow

### Initial Setup (One Time)
```powershell
# 1. Recommended one-command first run
.\run.ps1 -FirstRun

# OR manual setup path:
.\setup_venv.ps1

# 2. Install Tesseract OCR (external)
# Download from: https://github.com/UB-Mannheim/tesseract/wiki

# 3. Install Ollama + download model (external)
# Download from: https://ollama.com/
ollama pull qwen3:8b

# 4. (Optional) Calibrate OBS chat region for OCR fallback
.\run.ps1 -Calibrate
```

### Daily Streaming
```powershell
# 1. Start Ollama (if not auto-started)
ollama serve

# 2. Set Twitch API credentials (per terminal session)
$env:TWITCH_CLIENT_ID="YOUR_CLIENT_ID"
$env:TWITCH_CLIENT_SECRET="YOUR_CLIENT_SECRET"

# 3. First run only: authorize app in browser
.\run.ps1 -Preflight

# 4. Open OBS (chat overlay optional in Twitch-native mode)

# 5. Run the producer
.\run.ps1

# 4. Stream as normal - feedback plays in headphones
```

## ⚙️ Configuration Files

| File | Purpose | Edit? |
|------|---------|-------|
| `config/user_config.json` | Your settings | ✅ Yes - customize thresholds |
| `config/example_config.json` | Default template | ℹ️ Reference only |
| `requirements.txt` | Python packages | ⚠️ Only if adding dependencies |

## 🎛️ Key Settings to Tweak

Edit `config/user_config.json`:

```json
{
  // Chat source mode: "twitch" (default) or "ocr"
  "chat_ingestion_mode": "twitch",

  // Twitch channel name (required for twitch mode)
  "twitch_channel": "YOUR_TWITCH_CHANNEL",

  // Enable Twitch Helix stream stats polling
  "twitch_stats_enabled": true,
  "twitch_stats_poll_interval": 30.0,

  // Chat polling (used by OCR mode)
  "chat_poll_interval": 8.0,
  
  // Voice chunk size (lower = faster feedback, less accurate)
  "voice_chunk_duration": 10.0,
  
  // Feedback cooldown (min seconds between TTS)
  "feedback_cooldown": 60.0,
  
  // Speaking pace thresholds
  "words_per_min_min": 100,
  "words_per_min_max": 220,

  // Microphone selection: -1 auto-select (recommended)
  // Set to a device index to pin a specific stream mic
  "voice_input_device_index": -1,
  
  // Whisper model (tiny.en, small.en, medium.en)
  "whisper_model": "medium.en"
}
```

### Twitch API Credentials (Recommended: Environment Variables)

Do not store `Client Secret` in JSON files. Use environment variables.
Users should not create a new Twitch app; use the pre-registered app credentials provided by the maintainer.

Preferred: create `.env` from `.env.example` and fill values once.

```powershell
$env:TWITCH_CLIENT_ID="YOUR_CLIENT_ID"
$env:TWITCH_CLIENT_SECRET="YOUR_CLIENT_SECRET"
```

Optional persistent setup (new terminals):

```powershell
[System.Environment]::SetEnvironmentVariable("TWITCH_CLIENT_ID", "YOUR_CLIENT_ID", "User")
[System.Environment]::SetEnvironmentVariable("TWITCH_CLIENT_SECRET", "YOUR_CLIENT_SECRET", "User")
```

## 🐛 Quick Troubleshooting

| Problem | Solution |
|---------|----------|
| "Virtual environment not found" | Run `.\setup_venv.ps1` |
| "Script cannot be run" | Run `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| "Tesseract not found" | Install Tesseract and add to PATH |
| "Cannot connect to Ollama" | Run `ollama serve` in another terminal |
| "CUDA not available" | Install cuDNN libraries (see SETUP.md) |
| "No Twitch chat messages" | Set `twitch_channel` in `config/user_config.json` |
| "OCR not detecting chat" | Switch to `"chat_ingestion_mode": "ocr"` and re-run `.\run.ps1 -Calibrate` |
| "No audio in OBS" | Check Browser Source URL is `http://localhost:5000/player.html` |
| "Module not found" | Make sure venv is activated: `.\activate.ps1` |

## 📊 Checking Status

While running:

1. **Console output** - Watch for INFO messages
2. **Web health check** - Open `http://localhost:5000/health`
3. **Status flag** - Run with `.\run.ps1 -StatusInterval 30`
4. **Log file** - Check `logs/app.log`

## 🎮 OBS Setup Checklist

- [ ] `chat_ingestion_mode` and `twitch_channel` configured
- [ ] Twitch chat Browser Source added (only required for OCR mode)
- [ ] TTS player Browser Source (`http://localhost:5000/player.html`)
- [ ] TTS player audio monitoring set to "Monitor Only"
- [ ] Chat region calibrated (`.\run.ps1 -Calibrate`)

## 🔍 Testing Components

```powershell
# Fast non-interactive readiness check (recommended first)
.\test.ps1 smoke

# Test config system
.\test.ps1 config

# Test AI producer (requires Ollama)
.\test.ps1 ai

# Test all modules
.\test.ps1 all
```

## ✅ Test-First Validation Order (Make It Work First)

Run these in order and stop at first failure:

```powershell
# 1) Environment readiness (required)
.\test.ps1 smoke

# 2) Application preflight (required)
.\run.ps1 -Preflight

# 3) Core app startup for 60s (required)
.\run.ps1 -StatusInterval 15

# 4) Optional functional slices
.\test.ps1 ai
.\test.ps1 voice
.\test.ps1 tts
```

### Pass Criteria

- `smoke` returns all required checks as `PASS`
- `-Preflight` shows `✓ Config validation`, `✓ Ollama reachable`, `✓ Stream Safety Assessment`
- App runs for 60s without crashes/exceptions in `logs/app.log`
- Status output includes both sections:
  - `🛡️  Stream Safety`
  - `🔀 Inference Routing`

### Expected/Acceptable Early Failures

- `Twitch API authorization` can fail until `twitch_channel` is set to a real channel and OAuth is completed
- GPU metrics may be unavailable on non-NVIDIA systems (CPU/RAM safety checks remain valid)

## 📱 External Dependencies

Must be installed separately (not in requirements.txt):

1. **Tesseract OCR** - https://github.com/UB-Mannheim/tesseract/wiki
2. **Ollama + Qwen3:8B** - https://ollama.com/
3. **CUDA Toolkit + cuDNN** - https://developer.nvidia.com/cuda-downloads
4. **Microsoft C++ Build Tools** - https://visualstudio.microsoft.com/visual-cpp-build-tools/

## 📝 Roadmap TODO

- TODO: Resolve `twitch_channel` automatically from Twitch OAuth identity after login (remove manual channel entry).

## 🎯 Performance Tuning

| Goal | Change | Impact |
|------|--------|--------|
| Lower latency | Use `small.en` Whisper model | Less accurate transcription |
| Better accuracy | Use `medium.en` or `large` | Higher latency, more VRAM |
| Less feedback | Increase `feedback_cooldown` to 120s | Less interruptions |
| More sensitivity | Lower threshold values | More frequent feedback |
| Faster chat reading | Lower `chat_poll_interval` to 5s | Higher CPU usage |

## 📚 Documentation Guide

- **README.md** - Overview and features
- **QUICKSTART.md** - Step-by-step first run
- **SETUP.md** - Detailed installation steps
- **VENV_GUIDE.md** - Virtual environment help
- **MIGRATION.md** - Qwen2 → Qwen3 upgrade guide
- **PROMPTS.md** - AI prompt customization
- **QUICK_REFERENCE.md** - This file!

## 💡 Tips & Tricks

### Mic Not Detected
```powershell
# List input-capable audio devices
.\activate.ps1
python -c "import pyaudio; p=pyaudio.PyAudio(); [print(f'{i}: {p.get_device_info_by_index(i)[\"name\"]} | in={p.get_device_info_by_index(i)[\"maxInputChannels\"]}') for i in range(p.get_device_count()) if float(p.get_device_info_by_index(i).get('maxInputChannels',0) or 0) > 0]"
```

If running via Remote Desktop and default mic fails:
1. Find your preferred physical stream mic (or webcam/monitor mic fallback) from the list.
2. Set `voice_input_device_index` in `config/user_config.json` to that index.
3. Restart the app.

### Check GPU Usage
```powershell
# While producer is running
nvidia-smi
# Should show GPU utilization during Whisper/Ollama inference
```

### Reset Everything
```powershell
# Delete venv
Remove-Item -Recurse -Force venv

# Delete config
Remove-Item config\user_config.json

# Recreate
.\setup_venv.ps1
.\run.ps1 -Calibrate
```

### Change TTS Voice
1. Activate venv: `.\activate.ps1`
2. Run: `python -c "import pyttsx3; e=pyttsx3.init(); [print(f'{i}: {v.name}') for i,v in enumerate(e.getProperty('voices'))]"`
3. Edit `modules/tts_server.py` in `_init_tts()`:
   ```python
   voices = self.tts_engine.getProperty('voices')
   self.tts_engine.setProperty('voice', voices[1].id)  # Change index
   ```

## 🆘 Getting Help

1. Check relevant .md file from list above
2. Review `logs/app.log` for errors
3. Run with debug: `.\run.ps1 -Debug`
4. Check GitHub issues (if applicable)

---

**Remember:** Always use `.\run.ps1` or activate the venv with `.\activate.ps1` before running any Python commands!
