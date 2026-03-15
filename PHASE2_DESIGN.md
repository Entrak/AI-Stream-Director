# Phase 2 Design Document

## Phase 2a: Event-Driven Orchestration

### Current Architecture Issues

**Polling-Based Problems:**
- Main loop uses `time.sleep()` - wastes CPU cycles
- Blocking I/O in AI processing thread
- No natural backpressure mechanism
- Hard to prioritize events
- Difficult to track processing latency

**Current Flow:**
```
Main Thread (blocks on time.sleep)
  ↓
  → Poll chat_reader.get_recent_messages()
  → Poll voice_analyzer.get_average_metrics()
  → Check ai_producer.should_trigger()
  → Block on inference_router.generate_guidance()
  → Deliver via TTS
  → sleep(ai_processing_interval)
```

### Event-Driven Architecture

**Async Event Flow:**
```
Event Producers (async generators):
  - ChatEventProducer → chat_message events
  - VoiceEventProducer → transcription_complete events
  - SafetyEventProducer → safety_state_change events
  - StreamEventProducer → stream_stats_update events

      ↓ (publish to bounded queues)

Event Bus (asyncio.Queue with maxsize):
  - High priority: safety_state_change (maxsize=10)
  - Normal priority: chat_message, transcription (maxsize=100)
  - Low priority: stream_stats (maxsize=20)

      ↓ (backpressure drops low-priority when full)

Event Consumers (async workers):
  - GuidanceTriggerConsumer → aggregates events, checks triggers
  - InferenceConsumer → generates AI feedback (respects safety)
  - DeliveryConsumer → publishes to TTS/teleprompter
```

### Implementation Plan

**Step 1: Event Bus Foundation**
- Create `core/event_bus.py` with PriorityEventBus class
- Bounded queues per priority level
- Backpressure policy (drop vs block)
- Event type definitions (ChatEvent, VoiceEvent, SafetyEvent, etc.)

**Step 2: Event Producers**
- Refactor `TwitchChatReader` to emit events instead of polling
- Refactor `VoiceAnalyzer` to emit events on transcription
- Refactor `StreamSafetyManager` to emit on state changes
- Refactor `TwitchStreamStats` to emit periodic updates

**Step 3: Event Consumers**
- `GuidanceTriggerConsumer` - replaces `ai_producer.should_trigger()` logic
- `InferenceConsumer` - async wrapper around `inference_router.generate_guidance()`
- `DeliveryConsumer` - handles TTS/teleprompter publishing

**Step 4: Async Main Loop**
- Convert `main.py` to async/await
- Replace `_ai_processing_loop()` with event-driven orchestration
- Graceful shutdown with pending event drainage

### Event Schema

```python
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

class EventPriority(Enum):
    HIGH = 1      # Safety state changes, critical errors
    NORMAL = 2    # Chat messages, voice transcriptions
    LOW = 3       # Stream stats, metrics updates

class EventType(Enum):
    CHAT_MESSAGE = "chat_message"
    TRANSCRIPTION_COMPLETE = "transcription_complete"
    SAFETY_STATE_CHANGE = "safety_state_change"
    STREAM_STATS_UPDATE = "stream_stats_update"
    GUIDANCE_TRIGGERED = "guidance_triggered"
    INFERENCE_COMPLETE = "inference_complete"

@dataclass
class Event:
    type: EventType
    priority: EventPriority
    timestamp: float
    data: dict[str, Any]
    correlation_id: Optional[str] = None  # For tracing event chains
```

### Backpressure Strategy

**When Queues Fill:**
1. **HIGH priority** - Block producers (safety events must never be dropped)
2. **NORMAL priority** - Drop oldest events (chat history less critical)
3. **LOW priority** - Drop immediately (stats updates are continuous)

**Metrics to Track:**
- Events produced per second (by type)
- Events consumed per second (by type)
- Events dropped (by type, by reason)
- Queue depth over time
- Processing latency (p50, p95, p99)

### Migration Path (Backward Compatibility)

**Phase 2a.1: Hybrid Mode**
- Keep existing polling-based main.py
- Add new `main_async.py` with event-driven implementation
- Both modes share same modules
- Allow A/B testing before full cutover

**Phase 2a.2: Module Refactoring**
- Add async methods to existing modules (e.g., `TwitchChatReader.emit_events()`)
- Keep sync methods for backward compatibility
- Modules detect if called in async context

**Phase 2a.3: Full Cutover**
- Rename `main.py` → `main_legacy.py`
- Rename `main_async.py` → `main.py`
- Update `run.ps1` to use async entry point
- Deprecate legacy mode in docs

### Testing Strategy

**Unit Tests:**
- Event serialization/deserialization
- Queue backpressure behavior
- Event routing logic

**Integration Tests:**
- Producer → EventBus → Consumer flow
- Graceful shutdown with pending events
- Backpressure under load

**Performance Benchmarks:**
- Event throughput (events/sec)
- Latency (event emission → consumption)
- Memory usage under sustained load

## Phase 2b: OBS Integration

### BrowserSource Mode (Phase 2b.1)

**Current State:**
- TTS server already serves `/player.html` and `/teleprompter.html`
- These are functional BrowserSources

**Enhancements Needed:**
1. **Styling for OBS transparency** - Add alpha channel support
2. **Auto-hide behavior** - Cards fade out after configurable duration
3. **Multiple card layouts** - Grid view, single card, ticker tape
4. **CSS animations** - Smooth transitions, attention grabbers

**Files to Create:**
- `static/obs_teleprompter.html` - Optimized for OBS Studio
- `static/obs_metrics.html` - Live safety/routing metrics for streamer
- `config/obs_config.json` - BrowserSource layout presets

### OBS Dock Plugin (Phase 2b.2)

**Why OBS Dock?**
- Native integration (no separate browser needed)
- Access to OBS internals (scene control, source management)
- Richer UI (React components, real-time data binding)
- Streamer controls (pause AI, change priority, manual triggers)

**Plugin Architecture:**
```
OBS Studio (C++)
  ↓ (WebSocket/HTTP bridge)
obs-websocket plugin
  ↓ (JSON-RPC)
Streamer AI Producer (Python FastAPI)
  ↓ (REST API + SSE)
OBS Dock UI (HTML/CSS/JS + React)
```

**Features:**
1. **Live Metrics Dashboard** - Safety level, queue depth, event rate
2. **Manual Guidance Triggers** - Force specific advice types
3. **Priority Overrides** - Pause low-priority, boost high-priority
4. **Historical View** - Last 10 guidance cards, delivery success rate
5. **Config Editor** - In-app tweaking without file editing

**Files to Create:**
- `modules/obs_bridge.py` - FastAPI server for OBS integration
- `static/obs_dock/index.html` - React app entry point
- `static/obs_dock/components/` - React components
- `scripts/install_obs_plugin.ps1` - OBS dock installer

## Phase 2c: Provider Expansion

### Auto-Detect Cloud Providers

**Current:**
- Only Ollama provider auto-registered
- OpenAI/Anthropic require manual code changes

**Phase 2c.1: Config-Based Provider Detection**
```json
{
  "llm_providers": {
    "ollama": {
      "enabled": true,
      "priority": 1,
      "endpoint": "http://localhost:11434",
      "model": "qwen3:8b"
    },
    "openai": {
      "enabled": false,  // Auto-enable if OPENAI_API_KEY in .env
      "priority": 2,
      "model": "gpt-4o-mini"
    },
    "anthropic": {
      "enabled": false,  // Auto-enable if ANTHROPIC_API_KEY in .env
      "priority": 3,
      "model": "claude-3-5-haiku-20241022"
    }
  }
}
```

**Implementation:**
- Extend `ProviderRegistry` to read config
- Auto-register providers if credentials present
- Allow runtime enable/disable via API

### Streaming Response Handling

**Current:**
- Ollama generates full response, then returns
- No partial results during generation

**Phase 2c.2: Streaming Inference**
```python
async def generate_guidance_stream(prompt: str) -> AsyncIterator[str]:
    """Stream partial results as they're generated."""
    async for chunk in provider.generate_stream(prompt):
        yield chunk  # Emit to EventBus for real-time display
```

**Benefits:**
- Lower perceived latency (first words appear faster)
- Better UX in OBS Dock (show generation progress)
- Allow early cancellation (if unsafe state detected mid-generation)

### Cost Tracking

**Phase 2c.3: Token Usage Monitoring**
```python
@dataclass
class CostMetrics:
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    timestamp: float
```

**Features:**
- Track per-provider token usage
- Estimate costs (OpenAI/Anthropic have published pricing)
- Alert if approaching budget threshold
- Daily/weekly usage reports

## Phase 2d: Secrets Hardening

### Current Security Issues

**Problem:**
- `config/user_config.json` stores tokens in plaintext
- `twitch_user_access_token` visible in file system
- Easy to accidentally commit secrets to git

### Phase 2d.1: OS Credential Storage

**Windows: Windows Credential Manager**
```python
import keyring

# Store
keyring.set_password("StreamerAI", "twitch_access_token", token)

# Retrieve
token = keyring.get_password("StreamerAI", "twitch_access_token")
```

**Migration:**
1. Detect plaintext tokens in config
2. Prompt user to migrate to secure storage
3. Clear tokens from config file
4. Update modules to read from keyring

### Phase 2d.2: .env File Security

**Current:**
- `TWITCH_CLIENT_SECRET` in `.env` (git-ignored, but risky)

**Better:**
- Use environment variables from secure shell config
- OR use keyring for client secrets too
- Document secure deployment practices

## Phase 2e: Test Suite + CI Gates

### Unit Tests

**Coverage Targets:**
- `core/event_bus.py` - 100% (critical path)
- `modules/adaptive_inference_router.py` - 90%
- `modules/stream_safety_manager.py` - 90%
- `modules/llm_provider.py` - 85%

**Test Files to Create:**
- `tests/unit/test_event_bus.py`
- `tests/unit/test_safety_manager.py`
- `tests/unit/test_router.py`
- `tests/unit/test_providers.py`

### Integration Tests

**Scenarios:**
- End-to-end event flow (chat → guidance → TTS)
- Backpressure under load (queue overflow handling)
- Provider fallback (Ollama down → OpenAI succeeds)
- Graceful degradation (unsafe state → skip inference)

**Files:**
- `tests/integration/test_event_flow.py`
- `tests/integration/test_backpressure.py`
- `tests/integration/test_fallback.py`

### CI Pipeline

**GitHub Actions Workflow:**
```yaml
name: CI

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r requirements.txt -r requirements-dev.txt
      - run: pytest tests/ --cov=modules --cov=core --cov-report=xml
      - uses: codecov/codecov-action@v4
        with:
          file: ./coverage.xml
```

**Gates:**
- All tests must pass
- Code coverage ≥ 85%
- No linting errors (ruff)
- Type checking passes (mypy)

## Implementation Timeline

### Week 1: Event System Foundation
- ✅ Design document (this file)
- ⏸️ Implement `core/event_bus.py`
- ⏸️ Add event types to `core/events.py`
- ⏸️ Unit tests for EventBus

### Week 2: Producer Refactoring
- ⏸️ TwitchChatReader async events
- ⏸️ VoiceAnalyzer async events
- ⏸️ StreamSafetyManager async events
- ⏸️ Integration tests

### Week 3: Consumer + Main Loop
- ⏸️ Event consumer implementations
- ⏸️ Async main.py
- ⏸️ End-to-end validation
- ⏸️ Performance benchmarks

### Week 4: OBS Integration
- ⏸️ BrowserSource enhancements
- ⏸️ OBS Dock plugin (basic)
- ⏸️ FastAPI bridge server

### Week 5: Provider Expansion + Hardening
- ⏸️ Config-based provider detection
- ⏸️ Streaming response handling
- ⏸️ Secrets migration to keyring
- ⏸️ Cost tracking

### Week 6: Testing + Polish
- ⏸️ Full test suite
- ⏸️ CI pipeline setup
- ⏸️ Documentation updates
- ⏸️ Performance tuning

## Success Criteria

**Phase 2a Complete When:**
- [ ] Main loop runs async/await (no blocking sleep)
- [ ] All events flow through EventBus
- [ ] Backpressure works under load (no memory leaks)
- [ ] Processing latency p95 < 100ms
- [ ] Test coverage ≥ 85%

**Phase 2b Complete When:**
- [ ] OBS BrowserSource templates functional
- [ ] OBS Dock plugin installable
- [ ] Streamer can pause/resume AI via dock
- [ ] Live metrics visible in OBS

**Phase 2 Complete When:**
- [ ] All Phase 2a-2e items ✅
- [ ] No regressions in Phase 1 functionality
- [ ] Production deployment successful
- [ ] User docs updated

---

**Started:** 2026-03-08  
**Status:** In Progress (Phase 2a - Event System Foundation)  
**Owner:** Streamer AI Producer Team
