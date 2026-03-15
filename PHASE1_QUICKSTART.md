# Phase 1 Quick Start: Testing Stream-Safe Inference

## Installation

1. **Update dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

2. **Start Ollama** (if not already running):
   ```bash
   ollama serve
   # In another terminal, pull the model if needed:
   ollama pull qwen:8b
   ```

## Testing Stream Safety Manager

### Test 1: Quick Resource Monitor
```bash
python -c "
from modules.stream_safety_manager import StreamSafetyManager
import time

print('Starting Stream Safety Manager...')
safety = StreamSafetyManager()
safety.start_monitoring()

time.sleep(2)  # Let it collect samples

headroom = safety.get_headroom()
print(f'\n📊 Resource Headroom Snapshot:')
print(f'  CPU:    {headroom.cpu_available:.1f}% available ({headroom.cpu_percent:.1f}% in use)')
print(f'  Memory: {headroom.memory_available:.1f}% available ({headroom.memory_percent:.1f}% in use)')
if headroom.has_gpu:
    print(f'  GPU:    {headroom.gpu_available:.1f}% available ({headroom.gpu_percent:.1f}% in use)')
else:
    print(f'  GPU:    [Not available]')

level = safety.assess_safety()
stats = safety.get_stats()

print(f'\n🎯 Safety Assessment:')
print(f'  Current Level: {level.value}')
print(f'  Total Checks:  {stats[\"checks_total\"]}')
print(f'  Unsafe Events: {stats[\"unsafe_triggers\"]}')

constraints = safety.get_inference_constraints()
print(f'\n⚙️  Inference Constraints:')
print(f'  Max Context Tokens:   {constraints[\"max_context_tokens\"]}')
print(f'  Max Response Tokens:  {constraints[\"max_response_tokens\"]}')

safety.stop_monitoring()
print('\n✓ Test complete')
"
```

### Test 2: Preflight Check with Safety Assessment
```bash
python main.py --preflight
```

This will:
- Validate configuration
- Check Ollama connectivity
- Verify Twitch credentials
- Assess current stream safety
- Recommend safe mode if needed

Expected output:
```
PREFLIGHT CHECK
============================================================
✓ Config validation
✓ Ollama reachable
✓ Twitch credentials present
✓ Twitch API authorization
✓ Stream Safety Assessment
  → Safety level: safe | CPU: 45% avail | RAM: 62% avail

Setup marked as completed.
============================================================
```

## Testing Adaptive Inference Router

### Test 3: Router Initialization and Stats
```bash
python -c "
from modules.adaptive_inference_router import AdaptiveInferenceRouter
from modules.stream_safety_manager import StreamSafetyManager
from modules.llm_provider import get_global_registry, OllamaProvider

print('Initializing Adaptive Inference Router...')

# Create router
router = AdaptiveInferenceRouter()
router.start()

import time
time.sleep(1)

# Get stats
stats = router.get_stats()

print(f'\n📊 Router Statistics:')
print(f'  Total Requests:    {stats[\"total_requests\"]}')
print(f'  Successful:        {stats[\"successful\"]}')
print(f'  Skipped:           {stats[\"skipped\"]}')
print(f'  Fallbacks Used:    {stats[\"fallbacks_used\"]}')
print(f'  Success Rate:      {stats[\"success_rate\"]:.1f}%')

print(f'\n🛡️  Current Safety Status (from stats):')
safety_stats = stats['safety_manager_stats']
print(f'  Safety Level:      {safety_stats[\"safety_level\"]}')
print(f'  CPU Available:     {safety_stats[\"headroom\"][\"cpu_available\"]:.1f}%')
print(f'  Memory Available:  {safety_stats[\"headroom\"][\"memory_available\"]:.1f}%')

router.stop()
print('\n✓ Test complete')
"
```

## Testing LLM Provider Abstraction

### Test 4: Provider Registry and Fallback Chain
```bash
python -c "
from modules.llm_provider import get_global_registry, OllamaProvider, OpenAIProvider

print('Configuring Provider Registry...')

registry = get_global_registry()

# Register Ollama (always available locally)
ollama = OllamaProvider(model='qwen:8b')
registry.register('ollama', ollama)

print(f'\n📋 Registered Providers:')
providers = registry.list_providers()
for name, info in providers.items():
    print(f'  {name:15} | Type: {info[\"type\"]:15} | Available: {\"✓\" if info[\"available\"] else \"✗\"}')

# Set fallback chain
registry.set_fallback_chain(['ollama'])  # For now, just local
print(f'\n🔄 Fallback Chain: {\" → \".join([\"ollama\"])}')

# Get available provider
available = registry.get_available_provider()
print(f'\n✓ Selected Provider: {available.name}')
"
```

## Full System Test (Inference with Safety)

### Test 5: End-to-End Inference
```bash
python -c "
from modules.adaptive_inference_router import AdaptiveInferenceRouter
from modules.stream_safety_manager import StreamSafetyManager
from modules.llm_provider import OllamaProvider, get_global_registry
import time

print('Starting full system test...\n')

# Setup providers
registry = get_global_registry()
registry.register('ollama', OllamaProvider(model='qwen:8b'))
registry.set_fallback_chain(['ollama'])

# Create router
router = AdaptiveInferenceRouter()
router.start()

time.sleep(1)

# Attempt guidance generation
print('🤖 Attempting guidance generation...')
response = router.generate_guidance(
    prompt='Chat is slow. Suggest an engaging question to ask your audience.',
    system_prompt='You are a Twitch stream coach. Give 1-2 actionable tips under 50 words.',
    context_data={'chat_msgs': 3, 'viewers': 85}
)

if response:
    print(f'\n✓ Generation successful!')
    print(f'  Provider: {response.provider}')
    print(f'  Latency:  {response.latency_sec:.2f}s')
    print(f'  Text:     {response.text}')
else:
    print('\n⏸️  Generation skipped to protect stream')

# Show final stats
stats = router.get_stats()
print(f'\n📊 Final Router Stats:')
print(f'  Success Rate: {stats[\"success_rate\"]:.1f}%')
print(f'  Total Requests: {stats[\"total_requests\"]}')

router.stop()
print('\n✓ Test complete')
"
```

## Running the Full Application with Phase 1

### Start the Application
```bash
# Full output with status updates every 30 seconds
python main.py --status-interval 30

# Or quiet mode (no status output)
python main.py
```

### Monitor Output
Look for:
- ✓ initialization messages (safety manager, router, providers)
- 🛡️ Stream Safety section in status output
- 🔀 Inference Routing metrics

Example status:
```
🛡️  Stream Safety:
  Safety level: safe
  CPU available: 45.3%
  Memory available: 62.1%
  Safety checks total: 234
  Unsafe triggers: 0

🔀 Inference Routing:
  Total requests: 18
  Successful: 17
  Skipped: 1
  Fallbacks: 0
  Success rate: 94.4%
```

## Troubleshooting

### Issue: `Import "psutil" could not be resolved`
**Solution**: Make sure you've run `pip install -r requirements.txt` in your virtual environment.

### Issue: Safety level shows "degraded" or "minimal"
**Solution**: This is expected if your system is under load. The assistant will reduce its resource usage. Close other applications if you want "safe" mode.

### Issue: Router skips all requests
**Solution**: Check `router.get_stats()` for skip_reason. Common causes:
- Ollama not running (`ollama serve` in separate terminal)
- Provider registry not configured correctly

### Issue: Preflight shows GPU as unavailable
**Solution**: Normal if `nvidia-ml-py` is not installed or NVIDIA drivers missing. The system will fall back to CPU monitoring only.

## Next Steps

Once Phase 1 features are verified:

1. **Phase 2a**: Event-driven orchestration (replace polling with async/await)
2. **Phase 2b**: OBS integration (BrowserSource → OBS Dock)
3. **Phase 3**: Cloud provider fallback (add OpenAI, Anthropic)
4. **Phase 4**: Advanced features (custom plugins, metrics dashboard)

See [PHASE1_IMPLEMENTATION.md](PHASE1_IMPLEMENTATION.md) for complete documentation.
