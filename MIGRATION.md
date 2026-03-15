# Migration Guide: Qwen2 → Qwen3

## What Changed?

The Twitch AI Stream Producer has been upgraded to use **Qwen3:8B** instead of Qwen2:8B for improved AI feedback generation.

## Why Qwen3?

Qwen3 offers several improvements over Qwen2:

- ✅ **Better instruction following** - More accurate, concise responses
- ✅ **Improved context understanding** - Better analyzes chat + voice metrics
- ✅ **Enhanced multilingual support** - Better for non-English streams
- ✅ **Faster inference** - Optimized model architecture
- ✅ **Better reasoning** - More logical, actionable suggestions

## Migration Steps

### If You Haven't Installed Yet

Simply follow the normal setup instructions - everything is already configured for Qwen3:8B.

```powershell
.\setup_venv.ps1
ollama pull qwen3:8b
.\run.ps1 -Calibrate
.\run.ps1
```

### If You Already Have Qwen2:8B Installed

#### Option 1: Keep Both Models (Recommended)

You can keep Qwen2 and add Qwen3 alongside it:

```powershell
# Download Qwen3 (will coexist with Qwen2)
ollama pull qwen3:8b

# The app will automatically use Qwen3 (it's now the default)
.\run.ps1
```

**Storage**: Each model is ~5-6GB. If space is limited, proceed to Option 2.

#### Option 2: Replace Qwen2 with Qwen3

```powershell
# 1. Download Qwen3
ollama pull qwen3:8b

# 2. Remove Qwen2 (optional - saves ~5GB)
ollama rm qwen2:8b

# 3. Run the app
.\run.ps1
```

### Updating Existing Configuration

If you have a custom `config/user_config.json`, update the model reference:

**Before:**
```json
{
  "ollama_model": "qwen2:8b",
  ...
}
```

**After:**
```json
{
  "ollama_model": "qwen3:8b",
  ...
}
```

Or simply delete `config/user_config.json` and let the app recreate it with defaults.

## Verifying the Upgrade

### 1. Check Ollama Models

```powershell
ollama list
```

Should show:
```
NAME         ID              SIZE    MODIFIED
qwen3:8b     abc123def456    5.5 GB  2 minutes ago
```

### 2. Check App Configuration

```powershell
.\activate.ps1
python -c "from config.config import get_config; print(f'Model: {get_config().ollama_model}')"
```

Should output:
```
Model: qwen3:8b
```

### 3. Test Feedback Generation

```powershell
.\test.ps1 ai
```

Should connect to Ollama and generate test feedback using Qwen3.

## Troubleshooting

### "Model 'qwen3:8b' not found"

Download the model:
```powershell
ollama pull qwen3:8b
```

### "Cannot connect to Ollama"

Ensure Ollama service is running:
```powershell
# Start Ollama
ollama serve

# In another terminal, verify
ollama list
```

### App still using Qwen2

Check your config:
```powershell
# View current config
Get-Content config\user_config.json | Select-String "ollama_model"

# If it shows qwen2, manually edit or delete the file
Remove-Item config\user_config.json

# Restart app (will create new config with Qwen3 default)
.\run.ps1
```

### Feedback quality seems different

Qwen3 may produce slightly different feedback styles. You can:

1. **Adjust prompts** - See [PROMPTS.md](PROMPTS.md) for customization
2. **Tune temperature** - Edit `modules/ai_producer.py`:
   ```python
   options={
       'temperature': 0.7,  # Try 0.5-0.9
       ...
   }
   ```
3. **Compare models** - Switch back to Qwen2 temporarily to compare:
   ```json
   {"ollama_model": "qwen2:8b"}
   ```

## Model Comparison

| Feature | Qwen2:8B | Qwen3:8B |
|---------|----------|----------|
| Model Size | ~4.7 GB | ~5.5 GB |
| Context Window | 32K tokens | 128K tokens |
| Instruction Following | Good | Excellent |
| Response Speed | ~2-4s | ~1.5-3s (optimized) |
| Multilingual | 29 languages | 29 languages (improved) |
| Released | 2024 | 2025 |

## Compatibility

✅ **Fully backward compatible** - All existing prompts and configurations work with Qwen3

The only change needed is downloading the new model. All your:
- Custom prompts
- Configuration settings
- Calibrated chat regions
- Logs and data

...remain unchanged and compatible.

## Advanced: Using Other Models

Qwen3 comes in multiple sizes:

```powershell
# Larger, more capable (requires 16GB+ VRAM)
ollama pull qwen3:14b

# Smaller, faster (runs on 6GB VRAM)
ollama pull qwen3:4b
```

Update config to use them:
```json
{
  "ollama_model": "qwen3:14b"  // or "qwen3:4b"
}
```

**Note**: qwen3:14b provides better quality but is slower. qwen3:4b is faster but less accurate.

## Rollback (If Needed)

To revert to Qwen2:8B:

```powershell
# 1. Ensure Qwen2 is installed
ollama pull qwen2:8b

# 2. Edit config
# Change "ollama_model": "qwen3:8b" → "qwen2:8b"
notepad config\user_config.json

# 3. Restart app
.\run.ps1
```

## Summary

- ✅ **Default model upgraded** to Qwen3:8B for better performance
- ✅ **Fully compatible** with existing setups
- ✅ **Easy migration** - just `ollama pull qwen3:8b`
- ✅ **Can coexist** with Qwen2 if needed
- ✅ **Improved feedback quality** expected

**Recommended action**: Download Qwen3 and let the app use it automatically. Keep Qwen2 installed initially in case you want to compare.

---

For questions or issues, see [QUICK_REFERENCE.md](QUICK_REFERENCE.md) or [VENV_GUIDE.md](VENV_GUIDE.md).
