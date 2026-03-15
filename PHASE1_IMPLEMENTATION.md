# Phase 1 Implementation Summary: Stream-Safe Adaptive Inference

**Date**: March 8, 2026
**Status**: ✅ COMPLETE

## Overview

Phase 1 implements the **stream-safety-first** architecture that ensures the AI assistant never degrades viewer experience, even under resource constraints. The system now includes:

1. **Stream Safety Manager** - Real-time resource monitoring
2. **LLM Provider Abstraction** - Pluggable inference backends
3. **Adaptive Inference Router** - Intelligent provider/constraint selection
4. **Enhanced Preflight** - Stream safety assessment
5. **Degradation Ladder** - Deterministic quality reduction strategy

## What Was Implemented

### 1. Stream Safety Manager (`modules/stream_safety_manager.py`)

**Purpose**: Continuously monitor system resources and enforce hard guardrails on inference.

**Key Features**:
- Real-time CPU, GPU, RAM monitoring
- Automatic resource headroom calculation
- Safety level classification (SAFE → DEGRADED → MINIMAL → UNSAFE)
- Token/context constraint recommendations per safety level
- Background monitoring thread (500ms sample interval)
- Graceful fallback if GPU monitoring unavailable

**Usage**:
```python
from modules.stream_safety_manager import StreamSafetyManager

safety = StreamSafetyManager()
safety.start_monitoring()

# Check if stream is safe to run inference
if safety.stream_safe():
    # Safe to attempt generation
    level = safety.get_degradation_level()
    constraints = safety.get_inference_constraints()
    # Use constraints: max_context_tokens, max_response_tokens
else:
    # Skip inference to protect stream
    pass

safety.stop_monitoring()
```

**Safety Levels**:
- **SAFE**: >50% CPU available, >25% RAM available → Full 8K context, 200 tokens response
- **DEGRADED**: >25% CPU available → Reduced 2K context, 100 tokens response (local inference only)
- **MINIMAL**: >10% CPU available → Minimal 500 context, 50 tokens response (local only)
- **UNSAFE**: <10% available or >75% CPU in use → Skip inference entirely

### 2. LLM Provider Abstraction (`modules/llm_provider.py`)

**Purpose**: Support multiple inference backends (Ollama local, OpenAI cloud, Anthropic cloud) with a unified interface.

**Key Components**:
- `LLMProvider` abstract base class
- `OllamaProvider` (local, no API cost, always available)
- `OpenAIProvider` (cloud, high quality, graceful fallback)
- `ProviderRegistry` for provider discovery and fallback chains
- `LLMRequest` / `LLMResponse` normalization

**Usage**:
```python
from modules.llm_provider import (
    ProviderRegistry, OllamaProvider, OpenAIProvider,
    LLMRequest, get_global_registry
)

# Register providers
registry = get_global_registry()
registry.register("ollama", OllamaProvider(model="qwen:8b"))
registry.register("openai", OpenAIProvider(model="gpt-4-turbo"))

# Set fallback chain
registry.set_fallback_chain(["ollama", "openai"])  # Try local first, then cloud

# Request inference
request = LLMRequest(
    prompt="What's the vibe?",
    system_prompt="You are a stream coach.",
    context_tokens=2000,
    max_tokens=100,
)

provider = registry.get_available_provider()
response = provider.generate(request)
```

**Provider Interface**:
- `is_available()`: Can this provider service requests right now?
- `check_credentials()`: Are credentials valid?
- `generate(request)`: Synchronous inference
- `stream_generate(request)`: Streaming token generation
- `estimate_cost(prompt_tokens, completion_tokens)`: Cost estimation

### 3. Adaptive Inference Router (`modules/adaptive_inference_router.py`)

**Purpose**: Route inference requests through the system intelligently, enforcing safety constraints and fallback chains.

**Decision Flow**:
1. Check `stream_safe()` → If unsafe, skip and return None
2. Get safety level and associated constraints
3. Select best available provider from allowed list
4. Attempt inference with fallback chain
5. Record routing decision for analytics

**Key Classes**:
- `AdaptiveInferenceRouter`: Main orchestrator
- `DegradationLadder`: Constraint mappings (safety level → tokens/timeout)
- `RoutingDecision`: Analytics record for each attempt

**Usage**:
```python
from modules.adaptive_inference_router import AdaptiveInferenceRouter
from modules.stream_safety_manager import StreamSafetyManager
from modules.llm_provider import get_global_registry

router = AdaptiveInferenceRouter(
    safety_manager=StreamSafetyManager(),
    provider_registry=get_global_registry(),
)
router.start()  # Start background monitoring

# Later, in main loop:
response = router.generate_guidance(
    prompt="Chat is slow, suggest engagement.",
    system_prompt="You are a stream coach.",
    context_data={"chat_msgs": 5, "viewers": 120}
)

if response:
    # Safe to deliver
    tts_queue.put(response.text)
else:
    # Skipped to protect stream
    logger.info("Inference skipped, resources constrained")

router.stop()
```

**Output Metrics**:
```python
stats = router.get_stats()
# {
#   "total_requests": 42,
#   "successful": 40,
#   "skipped": 2,          # Stream protection triggered
#   "fallbacks_used": 1,   # Primary failed, used secondary
#   "success_rate": 95.2,
#   "recent_decisions": [...]
# }
```

### 4. Main Loop Integration (`main.py`)

**Changes**:
- Added `StreamSafetyManager` and `AdaptiveInferenceRouter` to `StreamProducer`
- Registered Ollama provider in `_init_components()`
- Updated `_ai_processing_loop()` to use router instead of direct AI producer calls
- Added safety manager monitoring start/stop in `start()` / `stop()`
- Enhanced `print_status()` with safety and routing metrics
- Augmented `run_preflight()` to assess stream safety and recommend safe mode

**Key Changes in AI Loop**:
```python
# OLD: Direct inference without safety checks
feedback = self.ai_producer.generate_feedback(chat_data, voice_data, new_users, recent_messages)

# NEW: Safety-aware inference routing
response = self.inference_router.generate_guidance(
    prompt=feedback_prompt,
    system_prompt="...",
    context_data={"chat": chat_data, "voice": voice_data}
)

if response and response.error is None:
    # Safe inference succeeded
    feedback = response.text
    # Deliver via TTS/teleprompter
else:
    # Skipped or failed - don't degrade stream
    logger.info("Inference skipped to protect stream")
```

### 5. Enhanced Preflight (`main.py` - `run_preflight()`)

**New Checks**:
- Stream Safety Assessment (displays safety level, CPU/RAM headroom)
- Resource warnings if degraded/minimal mode (user-facing guidance)
- Actionable hints for each failure

**Sample Output**:
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

If degraded:
```
⚠️  RESOURCE WARNING: Your system has moderate constraints.
  The assistant will use reduced context windows and response lengths.
  This ensures stream quality is never impacted.
```

## Dependencies Added

New packages in `requirements.txt`:
```
psutil==5.9.8              # System resource monitoring
openai==1.3.0              # Cloud provider support (optional)
nvidia-ml-py==12.535.108   # GPU monitoring (optional, graceful fallback)
```

Install with:
```bash
pip install -r requirements.txt
```

## Testing Phase 1 Features

### Test 1: Stream Safety Manager
```bash
python -c "
from modules.stream_safety_manager import StreamSafetyManager

safety = StreamSafetyManager()
safety.start_monitoring()

import time
time.sleep(1)

headroom = safety.get_headroom()
print(f'CPU available: {headroom.cpu_available:.1f}%')
print(f'RAM available: {headroom.memory_available:.1f}%')
print(f'Safety level: {safety.assess_safety().value}')

safety.stop_monitoring()
"
```

### Test 2: Preflight with Safety Check
```bash
# Run preflight to see stream safety assessment
python main.py --preflight
```

### Test 3: Adaptive Router Routing
```bash
python -c "
from modules.adaptive_inference_router import AdaptiveInferenceRouter
from modules.stream_safety_manager import StreamSafetyManager
from modules.llm_provider import get_global_registry, OllamaProvider

router = AdaptiveInferenceRouter()
stats = router.get_stats()
print('Router stats:', stats)
"
```

## Status Monitoring

When running (with `--status-interval 30`):
```
🛡️  Stream Safety:
  Safety level: safe
  CPU available: 45.3%
  Memory available: 62.1%
  GPU available: 60.5%
  Safety checks total: 145
  Unsafe triggers: 0

🔀 Inference Routing:
  Total requests: 42
  Successful: 40
  Skipped: 2
  Fallbacks: 1
  Success rate: 95.2%
```

## Next Steps (Phase 2)

Now that Phase 1 (stream-safety foundation) is complete, Phase 2 will focus on:

1. **Event-Driven Orchestration** - Replace polling with async events, bounded queues, backpressure
2. **OBS Integration (Dual Mode)**
   - Phase 2a: BrowserSource (stable, read-only cards)
   - Phase 2b: OBS Dock Plugin (richer control, real-time feedback)
3. **Secrets Hardening** - Move tokens from config to secure storage
4. **Enhanced Observability** - Metrics collection, dashboards, alerts
5. **Test Suite + CI** - Unit tests, integration tests, e2e validation

## Architecture Decision Log

**Why Stream Safety First?**
- Streaming is real-time: degradation is immediately visible to thousands of viewers
- Resource starvation is non-negotiable: better to skip advice than drop FPS
- Hard constraints > soft best-effort: hard guardrails protect reputation

**Why Provider Abstraction Now?**
- Cloud fallback (OpenAI, Anthropic) improves reliability without local GPU constraints
- Allows A/B testing local vs cloud quality/latency trade-offs
- Enables cost optimization (skip expensive cloud when local is safe)

**Why Degradation Ladder?**
- Better to give shorter advice than no advice
- Context reduction (remove recent history) is less visible than skipping entirely
- Deterministic policy is predictable for users and product analytics

## Verification Checklist

- [x] StreamSafetyManager monitors resources (CPU, GPU, VRAM, RAM)
- [x] SafetyLevel classification works (SAFE → DEGRADED → MINIMAL → UNSAFE)
- [x] LLMProvider abstraction supports multiple backends
- [x] ProviderRegistry enables fallback chains
- [x] AdaptiveInferenceRouter routes intelligently
- [x] Main loop uses router before inference
- [x] Preflight shows safety assessment
- [x] Status output includes safety/routing metrics
- [x] Dependencies added to requirements.txt
- [x] All modules pass syntax validation

## References

- Stream Safety Manager: [modules/stream_safety_manager.py](modules/stream_safety_manager.py)
- LLM Provider Abstraction: [modules/llm_provider.py](modules/llm_provider.py)
- Adaptive Inference Router: [modules/adaptive_inference_router.py](modules/adaptive_inference_router.py)
- Main Integration: [main.py](main.py#L1)
- Updated Dependencies: [requirements.txt](requirements.txt)
