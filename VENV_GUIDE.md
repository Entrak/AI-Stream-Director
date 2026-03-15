# Virtual Environment Setup - Summary

## ✅ What Was Configured

Your Twitch AI Stream Producer is now configured to run in an isolated virtual environment, preventing conflicts with system Python packages.

## 📂 New Files Created

### PowerShell Helper Scripts

1. **setup_venv.ps1** - One-command setup
   - Creates virtual environment
   - Installs all Python dependencies
   - Validates Python version
   - Shows setup status

2. **activate.ps1** - Quick venv activation
   - Activates virtual environment
   - Shows Python version and location
   - Displays helpful commands

3. **run.ps1** - Main run script
   - Activates venv automatically
   - Runs the application
   - Supports all command-line flags
   - Deactivates venv on exit

4. **test.ps1** - Module testing
   - Test individual components
   - Validates installation
   - Runs with venv activated

## 🚀 How to Use

### First-Time Setup

```powershell
# 1. Run setup (only needed once)
.\setup_venv.ps1

# 2. Install system dependencies (Tesseract, Ollama, CUDA)
#    See SETUP.md for details

# 3. Calibrate chat region
.\run.ps1 -Calibrate

# 4. Run the producer
.\run.ps1
```

### Daily Usage

```powershell
# Simple - just run the script
.\run.ps1

# With options
.\run.ps1 -Debug                    # Enable debug logging
.\run.ps1 -StatusInterval 60        # Print status every 60s
.\run.ps1 -Calibrate               # Re-run calibration
```

### Alternative: Manual Activation

```powershell
# Activate venv
.\activate.ps1

# Run any command
python main.py
python -m modules.chat_reader

# Deactivate when done
deactivate
```

## 🔍 Virtual Environment Benefits

### ✅ Isolation
- Project dependencies don't affect system Python
- Different projects can use different package versions
- No conflicts with other Python tools

### ✅ Reproducibility
- Same environment on any machine
- `requirements.txt` locks exact versions
- Easy to recreate if corrupted

### ✅ Clean Uninstall
- Delete `venv/` folder to completely remove
- No leftover packages in system Python

## 📦 What's Inside the venv/

```
venv/
├── Scripts/           # Executables (python.exe, pip.exe, activate scripts)
├── Lib/              # Python packages installed here
│   └── site-packages/
│       ├── flask/
│       ├── ollama/
│       ├── faster_whisper/
│       └── ... (all your dependencies)
├── Include/          # C headers for compilations
└── pyvenv.cfg       # Environment configuration
```

Size: ~2-3 GB (includes numpy, opencv, torch dependencies)

## 🛠️ Troubleshooting

### "Scripts cannot be run on this system"

PowerShell execution policy issue:

```powershell
# Check current policy
Get-ExecutionPolicy

# If Restricted, change to RemoteSigned
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### "Virtual environment not found"

Run setup:
```powershell
.\setup_venv.ps1
```

### "Package installation failed"

Common issues:

1. **PyAudio fails** - Needs C++ Build Tools
   ```powershell
   # After installing Build Tools, recreate venv
   .\setup_venv.ps1 -Force
   ```

2. **CUDA packages fail** - Install CUDA Toolkit first
   ```powershell
   # Then recreate venv
   .\setup_venv.ps1 -Force
   ```

3. **Network timeout** - Increase pip timeout
   ```powershell
   .\activate.ps1
   pip install --timeout=1000 -r requirements.txt
   ```

### "Module not found" when running

Venv not activated:
```powershell
# Always activate before running Python commands
.\activate.ps1

# Or use the run script which activates automatically
.\run.ps1
```

## 🔄 Recreating the Environment

If your venv gets corrupted or you want a fresh start:

```powershell
# Delete existing venv
Remove-Item -Recurse -Force venv

# Recreate (or use -Force flag)
.\setup_venv.ps1
```

## 🧪 Testing the Setup

```powershell
# Test configuration system
.\test.ps1 config

# Test all basic modules
.\test.ps1 all

# Test specific module
.\test.ps1 ai      # (requires Ollama running)
.\test.ps1 voice   # (requires microphone)
```

## 📊 Managing Dependencies

### Adding New Packages

```powershell
# Activate venv
.\activate.ps1

# Install new package
pip install package_name

# Update requirements.txt
pip freeze > requirements.txt
```

### Updating Packages

```powershell
.\activate.ps1

# Update specific package
pip install --upgrade package_name

# Update all packages
pip install --upgrade -r requirements.txt
```

### Checking Installed Packages

```powershell
.\activate.ps1

# List all packages
pip list

# Show specific package info
pip show flask
```

## 🎯 Best Practices

1. **Always use the venv**
   - Use `.\run.ps1` for convenience
   - Or activate manually: `.\activate.ps1`

2. **Don't install packages globally**
   - Install in venv only: `.\activate.ps1` then `pip install ...`

3. **Keep requirements.txt updated**
   - After adding packages: `pip freeze > requirements.txt`

4. **Commit venv/ to .gitignore** ✅ (already done)
   - Don't version control the venv folder
   - Only version control `requirements.txt`

5. **Recreate on deployment**
   - On new machine: `.\setup_venv.ps1`
   - Ensures clean, consistent environment

## 🆘 Getting Help

If you encounter issues:

1. Check this file for common solutions
2. Review [SETUP.md](SETUP.md) for detailed installation steps
3. Check [QUICKSTART.md](QUICKSTART.md) for step-by-step guide
4. Review logs in `logs/app.log`
5. Run with debug: `.\run.ps1 -Debug`

## ✨ Summary

Your project is now configured for virtual environment usage:

- ✅ Isolated Python environment in `venv/`
- ✅ All dependencies installed locally
- ✅ Convenient PowerShell scripts for daily use
- ✅ No system Python conflicts
- ✅ Easy to recreate on other machines

**Ready to use!** Run `.\run.ps1` to start the Stream Producer.
