# Quick Start Guide - Twitch AI Stream Producer

## Prerequisites Checklist

Before running the application, ensure you have:

- [ ] Python 3.10+ installed
- [ ] NVIDIA GPU drivers updated
- [ ] Twitch channel name configured in `config/user_config.json`
- [ ] Tesseract OCR installed and in PATH (optional OCR fallback mode)
- [ ] Ollama installed and running
- [ ] Qwen3:8B model downloaded (`ollama pull qwen3:8b`)
- [ ] CUDA Toolkit + cuDNN libraries (for GPU acceleration)
- [ ] OBS with Twitch chat overlay configured

## Installation (5 Minutes)

### Step 1: Setup Python Environment (Automated)

**Option A: Automated Setup (Recommended)**

```powershell
# Navigate to project directory
cd "E:\Development\Streamer AI Producer"

# Run setup script (creates venv and installs dependencies)
.\setup_venv.ps1

# Recommended: one command for full first-run flow
.\run.ps1 -FirstRun
```

This script will:
- ✅ Check Python version (3.10+ required)
- ✅ Create virtual environment in `venv/`
- ✅ Upgrade pip
- ✅ Install all Python dependencies
- ✅ Show next steps

**Option B: Manual Setup**

```powershell
# Navigate to project directory
cd "E:\Development\Streamer AI Producer"

# Create virtual environment
python -m venv venv

# Activate virtual environment
.\venv\Scripts\Activate.ps1

# Upgrade pip
python -m pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

**Note:** Some packages (like PyAudio) may require Microsoft C++ Build Tools. If installation fails, see troubleshooting below.

### Step 2: Verify System Dependencies

```powershell
# Check Tesseract
tesseract --version
# Should output: tesseract v5.x.x

# Check Ollama
ollama list
# Should show: qwen3:8b

# Check CUDA (optional but recommended)
nvidia-smi
# Should show: GPU info with CUDA version
```

### Step 3: Configure Twitch Chat

Edit `config/user_config.json`:

```json
{
  "chat_ingestion_mode": "twitch",
  "twitch_channel": "YOUR_TWITCH_CHANNEL"
}
```

Optional authenticated mode:

```json
{
  "twitch_bot_username": "YOUR_BOT_USERNAME",
  "twitch_oauth_token": "oauth:YOUR_TOKEN"
}
```

### Step 3b: Configure Twitch API Credentials (.env recommended)

Use the pre-registered app credentials provided by the maintainer (users do not create a new app).

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set:

```env
TWITCH_CLIENT_ID=YOUR_CLIENT_ID
TWITCH_CLIENT_SECRET=YOUR_CLIENT_SECRET
```

Run preflight once to authorize in browser:

```powershell
.\run.ps1 -Preflight
```

### Step 4: Configure OBS

1. **Add Chat Overlay:**
   - Sources → Add → Browser Source
   - URL: `https://www.twitch.tv/popout/YOUR_USERNAME/chat`
   - Width: 400, Height: 800
   - Position in preview window (remember location for calibration)

2. **Add TTS Player:**
   - Sources → Add → Browser Source
   - URL: `http://localhost:5000/player.html`
   - Width: 800, Height: 600
   - **IMPORTANT:** Right-click → Filters → Advanced Audio Properties
   - Set Audio Monitoring: **"Monitor Only (mute output)"**
   - This ensures only you hear the feedback via headphones

### Step 5: (Optional) Calibrate Chat Region for OCR Fallback

**Using the run script (recommended):**

```powershell
.\run.ps1 -Calibrate
```

**Or manually:**

```powershell
# Activate venv first
.\activate.ps1

# Run calibration
python main.py --calibrate
```

1. Press ENTER to take screenshot
2. Click and drag to select chat region in the screenshot
3. Press 'c' to confirm, 'r' to reset, 'q' to quit
4. Preview will show selected region
5. Press 'y' to save

**Tips:**
- Make sure OBS is visible when you press ENTER
- Select the entire chat message area (not including header/scrollbar)
- Larger region = more messages but slower OCR
- Recommended minimum: 300x400 pixels

### Step 6: First Run

**Using the run script (recommended):**

```powershell
.\run.ps1
```

**Or manually:**

```powershell
# Activate venv
.\activate.ps1

# Start the producer
python main.py
```

You should see:
```
============================================================
STARTING STREAM PRODUCER
============================================================
✓ Chat reader initialized
✓ Voice analyzer initialized
✓ AI producer initialized
✓ TTS server initialized
============================================================
ALL SYSTEMS RUNNING
============================================================
TTS Player: http://localhost:5000/player.html
Health Check: http://localhost:5000/health
============================================================
```

### Step 6: Verify Everything Works

1. **Test TTS Server:**
   - Open `http://localhost:5000/player.html` in browser
   - Should show "Waiting for feedback..."

2. **Test Chat Reading:**
   - Send some test messages in your Twitch chat
   - Watch console logs for OCR output:
     ```
     INFO - Processed 2 new messages
     DEBUG - username: test message here
     ```

3. **Test Voice Analysis:**
   - Speak into your microphone
   - After ~10 seconds, check logs:
     ```
     INFO - Voice: 180 WPM, 2 fillers, energy=0.65
     ```

4. **Test AI Feedback:**
   - After cooldown period (~60s), you should see:
     ```
     INFO - Trigger: [reason]
     INFO - Generated feedback in 2.5s: [feedback text]
     INFO - ✓ Feedback delivered
     ```
   - Audio should play in OBS (headphones only)

## Troubleshooting First Run

### "Python not found" or "pip not found"

Ensure Python is in your PATH:
```powershell
# Check Python
python --version

# Add to PATH if needed
$env:PATH += ";C:\Python310\"
```

### "Virtual environment not found"

Run the setup script:
```powershell
.\setup_venv.ps1
```

### "PyAudio installation failed"

PyAudio requires Microsoft C++ Build Tools:
1. Download from: https://visualstudio.microsoft.com/visual-cpp-build-tools/
2. Install "Desktop development with C++"
3. Re-run: `.\setup_venv.ps1 -Force`

Or install pre-built wheel:
```powershell
# Activate venv first
.\activate.ps1

# Install from pipwin
pip install pipwin
pipwin install pyaudio
```

### "Tesseract not found"
```powershell
# Add Tesseract to PATH manually
$env:PATH += ";C:\Program Files\Tesseract-OCR"

# Make permanent:
[Environment]::SetEnvironmentVariable(
    "PATH",
    $env:PATH,
    "Machine"
)
```

### "Cannot connect to Ollama"
```powershell
# Start Ollama service
ollama serve

# In another terminal, verify:
ollama list
```

### "CUDA not available"
- Most common issue: **cuDNN not installed**
- Download cuDNN from: https://developer.nvidia.com/cudnn
- Extract to CUDA installation directory
- Restart terminal

### "No audio playing in OBS"
- Verify Browser Source URL is exactly: `http://localhost:5000/player.html`
- Check OBS audio monitoring is "Monitor Only"
- Test by opening URL in regular browser first
- Check Windows audio mixer - ensure browser is not muted

### "OCR not detecting messages"
- Re-run calibration: `python main.py --calibrate`
- Increase chat font size in OBS
- Use high contrast (white text on black background)
- Ensure chat widget is not overlapped by other sources

## Daily Usage

### Starting a Stream Session

**Option A: Using run script (easiest)**

```powershell
cd "E:\Development\Streamer AI Producer"
.\run.ps1
```

**Option B: Manual activation**

```powershell
cd "E:\Development\Streamer AI Producer"

# Activate venv
.\activate.ps1

# Start producer
python main.py

# When done, deactivate
deactivate
```

**Prerequisites:**
1. Ollama service running (`ollama serve` if not auto-started)
2. OBS open with chat overlay visible
3. Microphone connected

### Stopping a Session

- Press `Ctrl+C` in the producer terminal
- All components will shut down gracefully
- Audio files in `temp/` will be cleaned up automatically

### Checking Status

While running, press `Ctrl+C` once (doesn't stop, just shows status):
```
========================================================
STREAM PRODUCER STATUS
========================================================

📝 Chat Reader:
  Captures: 142
  Success rate: 73.2%
  Total messages: 87
  Unique users: 23

🎤 Voice Analyzer:
  Chunks processed: 45
  Avg transcription time: 1.2s
  Words/min: 175
  Filler count: 6

🤖 AI Producer:
  Total feedbacks: 8
  Time since last: 45s
========================================================
```

Or visit: `http://localhost:5000/health`

## Configuration Tips

Edit `config/user_config.json` to customize:

### For Better Performance (Lower Latency)
```json
{
  "whisper_model": "small.en",  // Faster than medium.en
  "chat_poll_interval": 5.0,    // More frequent OCR
  "voice_chunk_duration": 5.0   // Smaller audio chunks
}
```

### For Better Accuracy (Higher Quality)
```json
{
  "whisper_model": "medium.en",    // More accurate
  "chat_poll_interval": 10.0,      // Less frequent but more thorough
  "ocr_confidence_threshold": 80   // Higher confidence required
}
```

### For Less Frequent Feedback
```json
{
  "feedback_cooldown": 120.0,            // 2 minutes between feedbacks
  "words_per_min_min": 80,               // Wider acceptable range
  "words_per_min_max": 250,
  "max_filler_count_per_min": 15         // More lenient threshold
}
```

## Next Steps

- Read [README.md](README.md) for detailed feature overview
- See [PROMPTS.md](PROMPTS.md) to customize AI feedback style
- Check [SETUP.md](SETUP.md) for advanced configuration options
- Monitor `logs/app.log` for detailed diagnostics

## Getting Help

If you encounter issues:
1. Check `logs/app.log` for error details
2. Run with `--debug` flag: `python main.py --debug`
3. Review [SETUP.md](SETUP.md) troubleshooting section
4. Verify all prerequisites are installed correctly

---

**Ready to stream!** The producer will now monitor your chat and voice, providing AI-powered feedback only you can hear. 🎙️🤖
