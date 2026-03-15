# Twitch AI Stream Producer

**Local AI-powered stream producer assistant for Twitch streamers**

Get real-time feedback on your stream performance using computer vision, voice analysis, and local AI - entirely offline on your Windows PC.

## 🎯 What It Does

This application acts as your personal "stream producer," monitoring your stream and providing actionable feedback through private TTS audio that only you hear. It analyzes:

- **Chat Activity** (via native Twitch chat): Detects first-time chatters, slow chat periods, potential scammers
- **Voice Performance** (via local Whisper STT): Tracks speaking pace, filler words, energy levels
- **AI Feedback** (via local Ollama): Generates concise, actionable suggestions in real-time

### Example Feedback

- *"Welcome TestUser123 to the stream - give them a shoutout!"*
- *"Chat is quiet. Try asking: What game should we play next?"*
- *"You're speaking at 250 words per minute - slow down for clarity"*
- *"You've said 'um' 12 times in the last minute - try pausing instead"*

## ✨ Features

- ✅ **100% Local/Offline** - No cloud APIs, no internet required (after setup)
- ✅ **Isolated Virtual Environment** - No conflicts with system Python packages
- ✅ **GPU Accelerated** - Uses RTX 4070 for fast Whisper STT and Ollama inference
- ✅ **Native Twitch Chat Ingestion** - Reliable chat parsing without OCR errors
- ✅ **OCR Fallback Mode** - Keep OBS-region OCR as backup option
- ✅ **Private Audio Feedback** - TTS plays only in your headphones via OBS monitoring
- ✅ **Customizable Triggers** - Configure thresholds for pace, fillers, chat activity
- ✅ **Low Latency** - ~5-10 second feedback loop

## 🖥️ System Requirements

- **OS**: Windows 10/11
- **GPU**: NVIDIA RTX 4070 (or similar with 8GB+ VRAM)
- **RAM**: 16GB minimum
- **Python**: 3.10 or higher
- **Storage**: 10GB free (for models and dependencies)

## 🚀 Quick Start

### 1. Setup Virtual Environment (Recommended)

**Using the automated setup script:**

```powershell
# One-command setup (creates venv and installs dependencies)
.\setup_venv.ps1

# Recommended: full first-run (setup + model pull + preflight auth)
.\run.ps1 -FirstRun
```

**Or manually:**

```powershell
# Create virtual environment
python -m venv venv

# Activate it
.\venv\Scripts\Activate.ps1

# Install Python packages
pip install -r requirements.txt
```

### 2. Install System Dependencies

See [SETUP.md](SETUP.md) for detailed installation instructions:

- Tesseract OCR (optional fallback mode): https://github.com/UB-Mannheim/tesseract/wiki
- Ollama: https://ollama.com/download/windows
- CUDA Toolkit + cuDNN (for GPU acceleration)

### 3. Download AI Model

```powershell
# Pull Qwen3:8B model (~5.5GB)
ollama pull qwen3:8b
```

### 4. Configure Twitch Chat Source

Set your channel in `config/user_config.json`:

```json
{
  "chat_ingestion_mode": "twitch",
  "twitch_channel": "YOUR_TWITCH_CHANNEL",
  "twitch_bot_username": "",
  "twitch_oauth_token": ""
}
```

Anonymous mode works for public chat read access. Add `twitch_bot_username` + `twitch_oauth_token` if you want authenticated access.

### 4b. (Optional) Enable Twitch API Stream Stats

Set Twitch Helix credentials in `.env` (recommended):

1) Copy `.env.example` to `.env`
2) Set values:

```env
TWITCH_CLIENT_ID=YOUR_CLIENT_ID
TWITCH_CLIENT_SECRET=YOUR_CLIENT_SECRET
```

Or set as environment variables:

```powershell
$env:TWITCH_CLIENT_ID="YOUR_CLIENT_ID"
$env:TWITCH_CLIENT_SECRET="YOUR_CLIENT_SECRET"
```

Use the pre-registered app credentials provided for this project; end users do not need to create a new app on dev.twitch.com.

Run a one-time authorization check (opens Twitch login in browser):

```powershell
.\run.ps1 -Preflight
```

The app will then poll `helix/streams` and show live stream status + viewer count in periodic status output.

### 5. Setup OBS

1. Add a **Custom Browser Dock** in OBS (`View → Docks → Custom Browser Docks...`)
2. Set Dock URL to `http://localhost:5000/obs_dock.html`
3. (Optional) Add a **Browser Source** for private TTS audio at `http://localhost:5000/player.html`
4. If using player source audio, set monitoring to **"Monitor Only (mute output)"**

Quick helper script:

```powershell
.\scripts\install_obs_dock.ps1 -LaunchOBS
```

Dock features include:
- Pause/resume guidance
- In-ear and teleprompter lane toggles
- Manual trigger (normal/extensive) with focus-goal selector
- Safety banner + cooldown timers
- Last/recent guidance + pinning
- OBS reconnect button + connection diagnostics
- Scene debug panel + auto-coaching guardrail cancel
- Session recap export (JSON/Markdown)
- Hotkey bridge endpoint (`POST /api/hotkey`)

Optional scene-aware extensive coaching:
- Enable `obs_websocket_enabled` in config
- Set OBS websocket host/port/password
- Starting scenes trigger pep-talk + checklist mode
- BRB scenes trigger deeper review + clarification prompts
- Per-mode cooldowns and templates are configurable in `config/user_config.json`

### 6. (Optional) Calibrate Chat Region for OCR Fallback

**Using the run script:**
```powershell
.\run.ps1 -Calibrate
```

**Or manually:**
```powershell
.\activate.ps1  # Activate venv first
python main.py --calibrate
```

- Takes a screenshot of your screen
- Click and drag to select the chat region
- Saves coordinates automatically

### 6. Run the Producer

**Using the run script (recommended):**
```powershell
.\run.ps1
```

**Or manually:**
```powershell
.\activate.ps1  # Activate venv first
python main.py
```

The application will:
- Start monitoring chat via Twitch IRC (native)
- Start analyzing voice (Whisper every 10 seconds)
- Generate AI feedback when triggered
- Serve TTS audio at `http://localhost:5000/player.html`

## 📋 How It Works

```
┌─────────────────┐
│  OBS Preview    │
│  (Chat Overlay) │
└────────┬────────┘
         │ Screenshot (8s interval)
         ▼
┌─────────────────┐      ┌──────────────┐
│  Chat Reader    │──────▶  AI Producer │
│  (OCR Extract)  │      │  (Ollama)    │
└─────────────────┘      └──────┬───────┘
                                 │
┌─────────────────┐              │ Generate
│  Voice Analyzer │──────────────┤ Feedback
│  (Whisper STT)  │              │
└─────────────────┘              ▼
         ▲              ┌─────────────────┐
         │              │  TTS Server     │
    Microphone          │  (pyttsx3)      │
                        └────────┬────────┘
                                 │ Audio File
                                 ▼
                        ┌─────────────────┐
                        │  OBS Browser    │
                        │  (Monitor Only) │
                        └─────────────────┘
```

### Components

1. **Chat Reader** ([modules/chat_reader.py](modules/chat_reader.py))
   - Captures screenshots of OBS chat region using `mss`
   - Preprocesses images (contrast, thresholding) for better OCR
   - Extracts text using `pytesseract`
   - Parses messages with regex, deduplicates, tracks users

2. **Voice Analyzer** ([modules/voice_analyzer.py](modules/voice_analyzer.py))
   - Captures microphone audio with `PyAudio`
   - Transcribes using `faster-whisper` (GPU accelerated)
   - Analyzes speaking rate, filler words, pitch, energy

3. **AI Producer** ([modules/ai_producer.py](modules/ai_producer.py))
   - Combines chat and voice data
   - Checks triggers (new chatters, slow chat, pacing issues)
   - Generates feedback prompts for Ollama
   - Returns concise suggestions (<50 words)

4. **TTS Server** ([modules/tts_server.py](modules/tts_server.py))
   - Flask web server on `localhost:5000`
   - Generates TTS audio using `pyttsx3`
   - Serves audio files to OBS BrowserSource
   - Web player auto-plays new feedback

## ⚙️ Configuration

Edit [config/user_config.json](config/user_config.json) after first run:

```json
{
  "obs_window_title": "OBS",
  "chat_region": {
    "x": 100,
    "y": 200,
    "width": 300,
    "height": 400
  },
  "ollama_model": "qwen3:8b",
  "whisper_model": "medium.en",
  "words_per_min_min": 100,
  "words_per_min_max": 220,
  "max_filler_count_per_min": 10,
  "chat_slow_threshold": 3,
  "feedback_cooldown": 60.0
}
```

### Key Settings

- **chat_poll_interval**: Seconds between OCR screenshots (default: 8.0)
- **voice_chunk_duration**: Audio chunk size for Whisper (default: 10.0)
- **feedback_cooldown**: Min seconds between TTS feedbacks (default: 60.0)
- **whisper_model**: Options: `tiny.en`, `small.en`, `medium.en` (faster ← → more accurate)

## 📊 Usage Tips

### Improving OCR Accuracy

- Use high contrast for chat (white text on black background)
- Increase chat font size in OBS
- Ensure chat widget is always visible (not overlapped)
- Re-calibrate region if you resize/move chat

### Reducing Latency

- Use smaller Whisper model (`small.en` instead of `medium.en`)
- Decrease polling intervals (but increases CPU/GPU load)
- Reduce voice chunk duration (but decreases transcription accuracy)

### Managing Feedback Frequency

- Increase `feedback_cooldown` to reduce interruptions
- Adjust threshold values to trigger less often
- Disable specific triggers by modifying [modules/ai_producer.py](modules/ai_producer.py)

## 🔧 Troubleshooting

### OCR Not Detecting Messages

- Run calibration again: `python main.py --calibrate`
- Check Tesseract installation: `tesseract --version`
- Verify chat overlay is visible in OBS preview
- Try increasing contrast in chat widget CSS

### CUDA Not Working

- Install cuDNN libraries (most common issue)
- Verify GPU drivers: `nvidia-smi`
- Check CUDA toolkit: `nvcc --version`
- Restart terminal after PATH changes

### No Audio in OBS

- Verify Flask server started (check console logs)
- Open `http://localhost:5000/player.html` in browser to test
- Check OBS Browser Source URL
- Set audio monitoring to "Monitor Only"

### Ollama Connection Failed

- Start Ollama service: `ollama serve`
- Verify model downloaded: `ollama list`
- Check firewall not blocking port 11434

See [SETUP.md](SETUP.md) for detailed troubleshooting.

## 📁 Project Structure

```
Streamer AI Producer/
├── main.py                 # Application entry point
├── requirements.txt        # Python dependencies
├── setup_venv.ps1         # Automated venv setup script
├── activate.ps1           # Quick venv activation
├── run.ps1                # Run app with venv (recommended)
├── test.ps1               # Test individual modules
├── SETUP.md               # Detailed setup guide
├── QUICKSTART.md          # Step-by-step first run
├── VENV_GUIDE.md          # Virtual environment guide
├── MIGRATION.md           # Qwen2 → Qwen3 upgrade guide
├── PROMPTS.md             # AI prompt examples
├── config/
│   ├── config.py          # Configuration management
│   └── user_config.json   # User settings (auto-generated)
├── modules/
│   ├── chat_reader.py     # OCR-based chat extraction
│   ├── voice_analyzer.py  # Whisper STT + analysis
│   ├── ai_producer.py     # Ollama feedback generation
│   ├── tts_server.py      # Flask TTS server
│   └── setup_wizard.py    # Calibration wizard
├── venv/                  # Virtual environment (created by setup)
├── temp/                  # Temporary audio files
└── logs/                  # Application logs
```

## 🔄 Virtual Environment Scripts

The project includes PowerShell scripts for easy virtual environment management:

- **setup_venv.ps1** - Creates venv and installs all dependencies
  ```powershell
  .\setup_venv.ps1         # Create new venv
  .\setup_venv.ps1 -Force  # Recreate if exists
  ```

- **activate.ps1** - Quickly activate the virtual environment
  ```powershell
  .\activate.ps1           # Activate venv
  ```

- **run.ps1** - Run the application with venv activated
  ```powershell
  .\run.ps1                # Normal run
  .\run.ps1 -Calibrate     # Run calibration
  .\run.ps1 -Debug         # Debug mode
  ```

- **test.ps1** - Test individual modules
  ```powershell
  .\test.ps1 config        # Test config system
  .\test.ps1 ai            # Test AI producer
  ```

## 🎨 Customization

### Custom AI Prompts

Edit prompts in [modules/ai_producer.py](modules/ai_producer.py) or see [PROMPTS.md](PROMPTS.md) for examples:

```python
# Example: More friendly welcome messages
system_msg = (
    "You are an enthusiastic Twitch stream producer. "
    "Give warm, specific welcome messages for new chatters. "
    "Keep it under 30 words and use their username."
)
```

### Adding Custom Triggers

In [modules/ai_producer.py](modules/ai_producer.py), add to `should_trigger()`:

```python
# Example: Trigger on specific keywords in chat
if any("!feedback" in msg.message for msg in recent_messages):
    logger.info("Trigger: User requested feedback")
    return True
```

## 📝 License

MIT License - See [LICENSE](LICENSE) file for details.

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repository
2. Create a feature branch
3. Test thoroughly (especially OCR and GPU components)
4. Submit a pull request

## 🙏 Acknowledgments

- [Ollama](https://ollama.com/) - Local LLM inference
- [faster-whisper](https://github.com/guillaumekln/faster-whisper) - Efficient Whisper implementation
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) - OCR engine
- [pyttsx3](https://github.com/nateshmbhat/pyttsx3) - Text-to-speech

## 📧 Support

For issues and questions:
- Check [VENV_GUIDE.md](VENV_GUIDE.md) for virtual environment help
- Check [SETUP.md](SETUP.md) for installation help
- Review logs in `logs/app.log`
- Open an issue with log excerpts and system details

---

**Note**: This tool provides ~70% accurate chat reading via OCR. For mission-critical chat monitoring, consider using Twitch IRC API (requires internet). This project prioritizes offline operation over perfect accuracy.
