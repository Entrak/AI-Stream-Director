"""
Twitch AI Stream Producer - Async Event-Driven Main Orchestration (Phase 2a)

Event-driven architecture with async/await, replacing polling with reactive event flow.

Flow:
  Event Producers (modules) → EventBus → Event Consumers → Actions
  
  TwitchChatReader → CHAT_MESSAGE events
  VoiceAnalyzer → TRANSCRIPTION_COMPLETE events  
  StreamSafetyManager → SAFETY_STATE_CHANGE events
  
  GuidanceTriggerConsumer → GUIDANCE_TRIGGERED events
  InferenceConsumer → INFERENCE_COMPLETE events
  DeliveryConsumer → TTS/Teleprompter delivery
"""

import asyncio
import argparse
import logging
import os
import signal
import sys
import time
import uuid
from pathlib import Path
from typing import Optional, Literal, Any

from config.config import get_config_manager, get_config
from modules.twitch_chat_reader import TwitchChatReader
from modules.voice_analyzer import VoiceAnalyzer
from modules.ai_producer import AIProducer
import modules.tts_server as tts_server_module
from modules.twitch_stream_stats import TwitchStreamStats
from modules.stream_safety_manager import StreamSafetyManager
from modules.obs_scene_watcher import ObsSceneWatcher
from modules.session_history import SessionHistoryManager, StreamSession
from modules.stream_analyzer import StreamAnalyzer
from modules.adaptive_inference_router import AdaptiveInferenceRouter
from modules.llm_provider import (
    ProviderRegistry,
    OllamaProvider,
    get_global_registry,
)
from core.events import ChatSnapshot, VoiceSnapshot
from core.guidance_router import GuidanceRouter
from core.event_bus import get_event_bus, Event, EventType, EventPriority
from core.event_consumers import (
    GuidanceTriggerConsumer,
    InferenceConsumer,
    DeliveryConsumer,
)

# Setup logging
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'app_async.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


class AsyncStreamProducer:
    """
    Async event-driven application orchestrator for Phase 2a.
    
    Replaces polling-based main.py with reactive event-driven architecture.
    """
    
    def __init__(self):
        self.config_manager = get_config_manager()
        self.config = self.config_manager.get_config()
        
        # Event bus
        self.event_bus = get_event_bus()
        
        # Event producers (modules that emit events)
        self.chat_reader: Optional[TwitchChatReader] = None
        self.voice_analyzer: Optional[VoiceAnalyzer] = None
        self.safety_manager: Optional[StreamSafetyManager] = None
        
        # Supporting components
        self.tts_server: Optional[Any] = None
        self.twitch_stats: Optional[TwitchStreamStats] = None
        self.guidance_router: Optional[GuidanceRouter] = None
        self.inference_router: Optional[AdaptiveInferenceRouter] = None
        self.provider_registry: Optional[ProviderRegistry] = None
        self.obs_scene_watcher: Optional[ObsSceneWatcher] = None
        self.session_history_manager: Optional[SessionHistoryManager] = None
        self.current_session: Optional[StreamSession] = None
        self.stream_analyzer: Optional[StreamAnalyzer] = None
        
        # Session tracking
        self.session_viewers_seen: set[str] = set()  # Track unique viewers in this session
        self.last_scene_for_transition: Optional[str] = None  # For detecting scene transitions
        self.session_start_time = 0.0
        
        # Training mode
        self.training_mode_active = False  # If True, don't send real-time guidance, just collect data
        
        # Event consumers
        self.guidance_trigger_consumer: Optional[GuidanceTriggerConsumer] = None
        self.inference_consumer: Optional[InferenceConsumer] = None
        self.delivery_consumer: Optional[DeliveryConsumer] = None
        
        # Control
        self.running = False
        self.guidance_paused = False
        self.in_ear_lane_enabled = self.config.in_ear_enabled
        self.teleprompter_lane_enabled = self.config.teleprompter_enabled
        self.current_scene_name = ""
        self.current_scene_mode: Literal["normal", "starting", "brb"] = "normal"
        self.current_scene_match_pattern = ""
        self.scene_extensive_feedback_enabled = self.config.scene_extensive_feedback_enabled
        self.scene_auto_coaching_active = self.scene_extensive_feedback_enabled
        self._scene_feedback_last_sent: dict[str, float] = {"starting": 0.0, "brb": 0.0}
        self._manual_feedback_last_sent: dict[str, float] = {"normal": 0.0, "extensive": 0.0}
        self.pending_scene_guardrail_id = ""
        self.pending_scene_guardrail_due_at = 0.0
        self.pending_scene_guardrail_mode = ""
        self._pending_scene_guardrail_task: Optional[asyncio.Task] = None
        self.shutdown_event = asyncio.Event()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        
        logger.info("Async Stream Producer initialized (Phase 2a)")
    
    def _init_components(self) -> None:
        """Initialize all components (sync part)."""
        logger.info("Initializing components...")
        
        try:
            # Initialize chat reader
            try:
                self.chat_reader = TwitchChatReader(self.config)
                logger.info("✓ Twitch chat reader initialized")
            except Exception as e:
                logger.error(f"Failed to initialize chat reader: {e}")
                raise
            
            # Initialize voice analyzer
            try:
                self.voice_analyzer = VoiceAnalyzer(self.config)
                logger.info("✓ Voice analyzer initialized")
            except Exception as e:
                logger.warning(f"Voice analyzer initialization failed (graceful degradation): {e}")
                self.voice_analyzer = None
            
            # Initialize TTS server
            tts_server_class = getattr(tts_server_module, "TTSServer")
            self.tts_server = tts_server_class(self.config)
            logger.info("✓ TTS server initialized")
            
            # Initialize Twitch stream stats
            self.twitch_stats = TwitchStreamStats(self.config)
            logger.info("✓ Twitch stream stats initialized")
            
            # Initialize guidance router
            self.guidance_router = GuidanceRouter(self.config)
            logger.info("✓ Guidance router initialized")
            
            # Initialize session history manager
            self.session_history_manager = SessionHistoryManager(self.config.session_history_path)
            logger.info("✓ Session history manager initialized")
            
            # Initialize stream analyzer
            self.stream_analyzer = StreamAnalyzer()
            logger.info("✓ Stream analyzer initialized")
            
            # Start new session
            self.current_session = StreamSession(
                session_id=str(uuid.uuid4()),
                started_at=time.time(),
                channel=self.config.twitch_channel,
                training_mode=self.config.training_mode_enabled,
            )
            self.session_start_time = time.time()
            self.training_mode_active = self.config.training_mode_enabled
            logger.info(f"✓ New session started: {self.current_session.session_id} (training_mode={self.training_mode_active})")
            
            # Initialize safety manager
            self.safety_manager = StreamSafetyManager()
            logger.info("✓ Stream Safety Manager initialized")
            
            # Initialize provider registry
            self.provider_registry = get_global_registry()
            
            # Register Ollama provider
            ollama_provider = OllamaProvider(
                host=self.config.ollama_host,
                model=self.config.ollama_model
            )
            self.provider_registry.register("ollama", ollama_provider)
            self.provider_registry.set_fallback_chain(["ollama"])
            logger.info("✓ Provider Registry initialized")

            # Wire OBS dock control/status callbacks
            if not self.tts_server:
                raise RuntimeError("TTS server was not initialized")
            self.tts_server.set_dock_callbacks(
                status_provider=self.get_dock_status,
                set_paused_callback=self.set_guidance_paused,
                set_lanes_callback=self.set_lane_toggles,
                manual_trigger_callback=self.trigger_manual_guidance_sync,
                pin_guidance_callback=self.pin_guidance,
                unpin_guidance_callback=self.unpin_guidance,
                reconnect_obs_callback=self.reconnect_obs_watcher_sync,
                cancel_scene_guardrail_callback=self.cancel_scene_guardrail,
                hotkey_action_callback=self.handle_hotkey_action_sync,
                get_session_status_callback=self.get_session_status_sync,
                session_kickoff_callback=self.trigger_session_kickoff_sync,
                set_session_goals_callback=self.set_session_goals_sync,
                assign_scene_mode_callback=self.assign_scene_mode_sync,
                add_coaching_note_callback=self.add_coaching_note_sync,
                toggle_training_mode_callback=self.toggle_training_mode_sync,
                end_session_with_analysis_callback=self.end_session_with_analysis_sync,
            )
            
            # Initialize adaptive inference router
            self.inference_router = AdaptiveInferenceRouter(
                provider_registry=self.provider_registry,
                safety_manager=self.safety_manager
            )
            logger.info("✓ Adaptive Inference Router initialized")
            
            # Initialize event consumers
            self.guidance_trigger_consumer = GuidanceTriggerConsumer(self.config)
            self.inference_consumer = InferenceConsumer(self.inference_router, self.config)
            self.delivery_consumer = DeliveryConsumer(
                self.tts_server,
                self.guidance_router,
                should_deliver=lambda: not self.guidance_paused,
                lane_state_provider=lambda: (
                    self.in_ear_lane_enabled,
                    self.teleprompter_lane_enabled,
                ),
            )
            logger.info("✓ Event consumers initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}", exc_info=True)
            raise
    
    async def start(self) -> None:
        """Start all async components."""
        if self.running:
            logger.warning("Stream producer already running")
            return
        
        logger.info("="*60)
        logger.info("STARTING ASYNC STREAM PRODUCER (Phase 2a)")
        logger.info("="*60)
        
        self.running = True
        self.loop = asyncio.get_running_loop()
        
        # Start event bus
        await self.event_bus.start()
        logger.info("✓ Event bus started")

        if not self.chat_reader or not self.safety_manager or not self.tts_server or not self.twitch_stats:
            raise RuntimeError("Required components not initialized before start")
        
        # Start event producers (modules that emit events)
        await self.chat_reader.start_async(emit_events=True)
        logger.info("✓ Chat reader started (async event mode)")
        
        if self.voice_analyzer:
            await self.voice_analyzer.start_async(emit_events=True)
            logger.info("✓ Voice analyzer started (async event mode)")
        
        await self.safety_manager.start_monitoring_async(emit_events=True)
        logger.info("✓ Safety manager started (async event mode)")
        
        # Start TTS server (runs Flask in background thread)
        self.tts_server.start()
        logger.info("✓ TTS server started")
        
        # Start Twitch stats polling
        self.twitch_stats.start()
        logger.info("✓ Twitch stats started")

        # Start OBS scene watcher (optional)
        await self._start_obs_scene_watcher()
        
        logger.info("="*60)
        logger.info("ALL SYSTEMS RUNNING (Event-Driven Mode)")
        logger.info("="*60)
        logger.info(f"TTS Player: http://localhost:5000/player.html")
        logger.info(f"Teleprompter: http://localhost:5000/teleprompter.html")
        logger.info(f"OBS Dock: http://localhost:5000/obs_dock.html")
        logger.info(f"Health Check: http://localhost:5000/health")
        logger.info("="*60)
        
        # Wait for shutdown signal
        await self.shutdown_event.wait()
    
    async def stop(self) -> None:
        """Stop all async components gracefully."""
        if not self.running:
            return
        
        logger.info("Stopping async stream producer...")
        self.running = False
        
        # Stop event producers
        if self.chat_reader:
            await self.chat_reader.stop_async()
        
        if self.voice_analyzer:
            await self.voice_analyzer.stop_async()
        
        if self.safety_manager:
            await self.safety_manager.stop_monitoring_async()
        
        # Stop supporting components
        if self.tts_server:
            self.tts_server.stop()
        
        if self.twitch_stats:
            self.twitch_stats.stop()

        if self.obs_scene_watcher:
            await self.obs_scene_watcher.stop()

        self.cancel_scene_guardrail()
        
        # Stop event bus (drain pending events)
        await self.event_bus.stop(drain=True)
        
        logger.info("Async stream producer stopped")

    def set_guidance_paused(self, paused: bool) -> None:
        """Pause or resume guidance delivery from OBS dock control."""
        self.guidance_paused = paused
        state = "PAUSED" if paused else "RESUMED"
        logger.info(f"Operator set guidance delivery: {state}")

    def set_lane_toggles(self, in_ear_enabled: bool, teleprompter_enabled: bool, scene_extensive_enabled: bool) -> None:
        """Set lane enablement from dock controls."""
        self.in_ear_lane_enabled = in_ear_enabled
        self.teleprompter_lane_enabled = teleprompter_enabled
        self.scene_extensive_feedback_enabled = scene_extensive_enabled
        logger.info(
            "Operator lane toggles updated: "
            f"in_ear={self.in_ear_lane_enabled}, teleprompter={self.teleprompter_lane_enabled}, "
            f"scene_extensive={self.scene_extensive_feedback_enabled}"
        )

    def trigger_manual_guidance_sync(
        self,
        mode: str = "normal",
        intent: str = "general",
        focus_goal: str = "general",
    ) -> dict:
        """Thread-safe sync wrapper for manual trigger callback from Flask thread."""
        if not self.loop:
            return {"published": False, "mode": mode, "intent": intent, "error": "Event loop unavailable"}

        future = asyncio.run_coroutine_threadsafe(
            self.trigger_manual_guidance(mode=mode, intent=intent, focus_goal=focus_goal, source="manual"),
            self.loop,
        )
        try:
            return future.result(timeout=10)
        except Exception as exc:
            return {"published": False, "mode": mode, "intent": intent, "error": str(exc)}

    async def trigger_manual_guidance(
        self,
        mode: str = "normal",
        intent: str = "general",
        focus_goal: str = "general",
        source: str = "manual",
    ) -> dict:
        """Manually trigger guidance generation from dock control."""
        reason = f"Manual trigger ({intent})"
        now = time.time()

        if source == "manual":
            mode_key = "extensive" if mode == "extensive" else "normal"
            cooldown = self.config.manual_extensive_cooldown if mode_key == "extensive" else self.config.manual_normal_cooldown
            remaining = cooldown - (now - self._manual_feedback_last_sent.get(mode_key, 0.0))
            if remaining > 0:
                return {
                    "published": False,
                    "mode": mode,
                    "intent": intent,
                    "focus_goal": focus_goal,
                    "scene_mode": self.current_scene_mode,
                    "error": f"Manual trigger cooldown active ({remaining:.1f}s remaining)",
                }

        chat_snapshot = {
            "recent_message_count": 0,
            "total_messages": 0,
            "new_users": [],
            "recent_messages": [],
        }
        if self.chat_reader:
            try:
                chat_stats = self.chat_reader.get_stats()
                chat_snapshot["total_messages"] = chat_stats.get("total_messages", 0)
                chat_snapshot["recent_message_count"] = self.chat_reader.get_message_count(30.0)
                recent_messages = self.chat_reader.get_recent_messages(10)
                chat_snapshot["recent_messages"] = [
                    {
                        "username": msg.username,
                        "message": msg.message,
                        "timestamp": msg.timestamp,
                    }
                    for msg in recent_messages
                ]
            except Exception:
                pass

        voice_snapshot = {
            "words_per_minute": 0.0,
            "filler_count": 0,
            "energy_level": 0.0,
        }
        if self.voice_analyzer:
            try:
                voice_stats = self.voice_analyzer.get_average_metrics(60.0)
                voice_snapshot["words_per_minute"] = voice_stats.get("words_per_minute", 0.0)
                voice_snapshot["filler_count"] = voice_stats.get("filler_count", 0)
                voice_snapshot["energy_level"] = voice_stats.get("energy_level", 0.0)
            except Exception:
                pass

        trigger_event = Event(
            type=EventType.GUIDANCE_TRIGGERED,
            priority=EventPriority.NORMAL,
            data={
                "reason": reason,
                "chat_snapshot": chat_snapshot,
                "voice_snapshot": voice_snapshot,
                "mode": mode,
                "intent": intent,
                "focus_goal": focus_goal,
                "scene_mode": self.current_scene_mode,
                "manual_trigger": True,
                "timestamp": now,
            },
            source="obs_dock_manual_trigger",
        )

        published = await self.event_bus.publish(trigger_event)
        if published and source == "manual":
            mode_key = "extensive" if mode == "extensive" else "normal"
            self._manual_feedback_last_sent[mode_key] = now

        return {
            "published": published,
            "mode": mode,
            "intent": intent,
            "focus_goal": focus_goal,
            "scene_mode": self.current_scene_mode,
        }

    def reconnect_obs_watcher_sync(self) -> dict:
        """Thread-safe sync wrapper to reconnect OBS watcher from Flask thread."""
        if not self.loop:
            return {"ok": False, "error": "Event loop unavailable"}

        future = asyncio.run_coroutine_threadsafe(self.reconnect_obs_watcher(), self.loop)
        try:
            return future.result(timeout=10)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def reconnect_obs_watcher(self) -> dict:
        """Reconnect OBS scene watcher without restarting app."""
        if not self.config.obs_websocket_enabled:
            return {"ok": False, "error": "OBS websocket disabled in config"}

        if self.obs_scene_watcher:
            await self.obs_scene_watcher.stop()

        await self._start_obs_scene_watcher()
        return {"ok": True}

    def cancel_scene_guardrail(self) -> bool:
        """Cancel pending scene auto-coaching guardrail trigger."""
        if not self._pending_scene_guardrail_task:
            return False
        self._pending_scene_guardrail_task.cancel()
        self._pending_scene_guardrail_task = None
        self.pending_scene_guardrail_id = ""
        self.pending_scene_guardrail_due_at = 0.0
        self.pending_scene_guardrail_mode = ""
        logger.info("Pending scene guardrail trigger cancelled by operator")
        return True

    def handle_hotkey_action_sync(self, action: str) -> dict:
        """Thread-safe sync wrapper for hotkey action bridge endpoint."""
        if not self.loop:
            return {"ok": False, "action": action, "error": "Event loop unavailable"}

        future = asyncio.run_coroutine_threadsafe(self.handle_hotkey_action(action), self.loop)
        try:
            return future.result(timeout=10)
        except Exception as exc:
            return {"ok": False, "action": action, "error": str(exc)}

    async def handle_hotkey_action(self, action: str) -> dict:
        """Handle hotkey bridge actions from local endpoint."""
        raw_action = (action or "").strip()
        mapped_action = self.config.hotkey_actions.get(raw_action, raw_action)
        normalized = mapped_action.lower()
        if normalized == "pause_toggle":
            self.set_guidance_paused(not self.guidance_paused)
            return {"ok": True, "action": normalized, "guidance_paused": self.guidance_paused}
        if normalized == "manual_tip":
            result = await self.trigger_manual_guidance(mode="normal", intent="hotkey_tip", focus_goal="general", source="manual")
            return {"ok": result.get("published", False), "action": normalized, "result": result}
        if normalized == "manual_extensive":
            result = await self.trigger_manual_guidance(mode="extensive", intent="hotkey_extensive", focus_goal="general", source="manual")
            return {"ok": result.get("published", False), "action": normalized, "result": result}
        if normalized == "reconnect_obs":
            reconnect = await self.reconnect_obs_watcher()
            return {"ok": reconnect.get("ok", False), "action": normalized, "result": reconnect}
        if normalized == "cancel_scene_guardrail":
            cancelled = self.cancel_scene_guardrail()
            return {"ok": cancelled, "action": normalized}

        return {"ok": False, "action": normalized, "error": "Unknown hotkey action"}

    def pin_guidance(self, guidance_id: str) -> bool:
        """Pin a guidance entry by id."""
        if not self.tts_server:
            return False
        return self.tts_server.pin_guidance(guidance_id)

    def unpin_guidance(self, guidance_id: str) -> bool:
        """Unpin a guidance entry by id."""
        if not self.tts_server:
            return False
        return self.tts_server.unpin_guidance(guidance_id)

    async def _start_obs_scene_watcher(self) -> None:
        """Start optional OBS scene watcher if enabled in config."""
        if not self.config.obs_websocket_enabled:
            return

        self.obs_scene_watcher = ObsSceneWatcher(
            host=self.config.obs_websocket_host,
            port=self.config.obs_websocket_port,
            password=self.config.obs_websocket_password,
            poll_interval=self.config.obs_scene_poll_interval,
        )
        await self.obs_scene_watcher.start(self._on_scene_change)
        logger.info("✓ OBS scene watcher started")

    def _detect_scene_mode(self, scene_name: str) -> tuple[Literal["normal", "starting", "brb"], str]:
        """Classify scene name into normal/starting/brb mode and matched pattern."""
        scene_lower = scene_name.lower()

        for pattern in self.config.obs_brb_scene_patterns:
            if pattern.lower() in scene_lower:
                return "brb", pattern

        for pattern in self.config.obs_starting_scene_patterns:
            if pattern.lower() in scene_lower:
                return "starting", pattern

        return "normal", ""

    async def _on_scene_change(self, scene_name: str) -> None:
        """Handle scene changes from OBS watcher."""
        self.current_scene_name = scene_name
        scene_mode, matched_pattern = self._detect_scene_mode(scene_name)
        self.current_scene_mode = scene_mode
        self.current_scene_match_pattern = matched_pattern

        if scene_mode == "normal":
            return

        if not self.scene_auto_coaching_active:
            return

        if self.config.scene_auto_disable_on_disconnect and self.obs_scene_watcher and not self.obs_scene_watcher.connected:
            return

        now = time.time()
        last_sent = self._scene_feedback_last_sent.get(scene_mode, 0.0)
        mode_cooldown = self.config.scene_brb_cooldown if scene_mode == "brb" else self.config.scene_starting_cooldown
        if mode_cooldown <= 0:
            mode_cooldown = self.config.scene_extensive_feedback_cooldown

        if (now - last_sent) < mode_cooldown:
            return

        intent = "brb_review" if scene_mode == "brb" else "starting_peptalk"
        focus_goal = "goals" if scene_mode == "brb" else "energy"
        await self._schedule_scene_guardrail(scene_mode, intent, focus_goal)

    async def _schedule_scene_guardrail(self, scene_mode: str, intent: str, focus_goal: str) -> None:
        """Schedule delayed auto coaching so operator can cancel before send."""
        self.cancel_scene_guardrail()

        guardrail_id = uuid.uuid4().hex
        due_at = time.time() + self.config.scene_guardrail_countdown_sec
        self.pending_scene_guardrail_id = guardrail_id
        self.pending_scene_guardrail_due_at = due_at
        self.pending_scene_guardrail_mode = scene_mode

        async def _runner():
            try:
                await asyncio.sleep(self.config.scene_guardrail_countdown_sec)
                result = await self.trigger_manual_guidance(
                    mode="extensive",
                    intent=intent,
                    focus_goal=focus_goal,
                    source="scene_auto",
                )
                if result.get("published"):
                    self._scene_feedback_last_sent[scene_mode] = time.time()
                    logger.info(f"Triggered extensive scene coaching for {scene_mode} scene")
            except asyncio.CancelledError:
                pass
            finally:
                self.pending_scene_guardrail_id = ""
                self.pending_scene_guardrail_due_at = 0.0
                self.pending_scene_guardrail_mode = ""
                self._pending_scene_guardrail_task = None

        self._pending_scene_guardrail_task = asyncio.create_task(_runner(), name="scene_guardrail")

    def _build_safety_guard_banner(self) -> str:
        """Build explicit safety-block reason for dock display."""
        if not self.safety_manager:
            return "Safety manager unavailable"

        try:
            stats = self.safety_manager.get_stats()
            level = stats.get("safety_level", "unknown")
            headroom = stats.get("headroom", {})
            cpu_avail = headroom.get("cpu_available", 0.0)
            mem_avail = headroom.get("memory_available", 0.0)

            if level == "unsafe":
                return f"Inference blocked by safety: low headroom (CPU {cpu_avail:.1f}%, RAM {mem_avail:.1f}%)"
            if level == "minimal":
                return "Safety minimal mode: inference heavily restricted"
            if level == "degraded":
                return "Safety degraded mode: shorter and lower-frequency guidance"
            return "Safety clear"
        except Exception:
            return "Safety status unavailable"

    def assign_scene_mode_sync(self, scene_name: str, mode: str) -> dict:
        """Thread-safe sync wrapper to assign a scene to a mode (starting/brb/normal)."""
        if not self.loop:
            return {"ok": False, "error": "Event loop unavailable"}

        future = asyncio.run_coroutine_threadsafe(
            self.assign_scene_mode(scene_name, mode),
            self.loop,
        )
        try:
            return future.result(timeout=10)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def assign_scene_mode(self, scene_name: str, mode: str) -> dict:
        """Assign a scene name to a mode (starting/brb/normal) for manual override."""
        if mode not in ("starting", "brb", "normal"):
            return {"ok": False, "error": f"Invalid mode: {mode}. Must be 'starting', 'brb', or 'normal'"}

        self.config.scene_to_mode_mapping[scene_name] = mode
        self.config_manager.save()
        logger.info(f"Scene '{scene_name}' assigned to mode '{mode}'")
        return {"ok": True, "scene": scene_name, "mode": mode}

    def trigger_session_kickoff_sync(self, repeat_goals: bool = False) -> dict:
        """Thread-safe sync wrapper to trigger session kickoff sequence from Flask thread."""
        if not self.loop:
            return {"ok": False, "error": "Event loop unavailable"}

        future = asyncio.run_coroutine_threadsafe(
            self.trigger_session_kickoff(repeat_goals),
            self.loop,
        )
        try:
            return future.result(timeout=15)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def trigger_session_kickoff(self, repeat_goals: bool = False) -> dict:
        """Generate personalized session kickoff greeting and pep talk."""
        if not self.current_session:
            return {"ok": False, "error": "No active session"}

        if not self.session_history_manager:
            return {"ok": False, "error": "Session history manager unavailable"}

        # Get last session context
        last_session_context = ""
        last_session = self.session_history_manager.get_last_session()
        if last_session:
            last_session_context = self.session_history_manager.get_session_summary(last_session)

        # Determine what greeting to generate
        prompts = []

        # 1. Initial greeting
        prompts.append({
            "section": "greeting",
            "template": self.config.session_kickoff_greeting_template,
            "instruction": f"You are greeting the streamer on channel '{self.config.twitch_channel}'. Be warm and enthusiastic.",
        })

        # 2. Goals question (unless repeating last session's goals)
        if record_goals := not repeat_goals:
            prompts.append({
                "section": "goals_question",
                "template": self.config.session_kickoff_goals_question_template,
                "instruction": "Ask about their streaming goals and objectives for today. Be conversational and encouraging.",
            })

        # 3. Pep talk with coaching context from last session
        pep_talk_context = ""
        if last_session_context:
            pep_talk_context = f"\nLast session recap:\n{last_session_context}\n"

        prompts.append({
            "section": "pep_talk",
            "template": self.config.session_kickoff_pep_talk_template,
            "instruction": (
                f"Give a motivational pep talk with practical tips. {pep_talk_context}"
                f"Include a brief countdown reminder if 'starting' scene is expected soon."
            ),
        })

        # Generate each section through inference
        greeting_results = []
        for prompt_data in prompts:
            try:
                chat_snapshot = {"recent_messages": [], "total_messages": 0}
                if self.chat_reader:
                    try:
                        chat_stats = self.chat_reader.get_stats()
                        chat_snapshot["total_messages"] = chat_stats.get("total_messages", 0)
                    except Exception:
                        pass

                voice_snapshot = {"words_per_minute": 0.0, "energy_level": 0.0}
                if self.voice_analyzer:
                    try:
                        voice_stats = self.voice_analyzer.get_average_metrics(60.0)
                        voice_snapshot["words_per_minute"] = voice_stats.get("words_per_minute", 0.0)
                        voice_snapshot["energy_level"] = voice_stats.get("energy_level", 0.0)
                    except Exception:
                        pass

                trigger_event = Event(
                    type=EventType.GUIDANCE_TRIGGERED,
                    priority=EventPriority.HIGH,
                    data={
                        "reason": f"Session kickoff: {prompt_data['section']}",
                        "chat_snapshot": chat_snapshot,
                        "voice_snapshot": voice_snapshot,
                        "mode": "extensive",
                        "intent": f"kickoff_{prompt_data['section']}",
                        "focus_goal": "energy",
                        "scene_mode": "normal",
                        "manual_trigger": True,
                        "timestamp": time.time(),
                        "session_kickoff_section": prompt_data['section'],
                        "session_kickoff_instruction": prompt_data['instruction'],
                        "session_kickoff_template": prompt_data['template'],
                    },
                    source="session_kickoff",
                )

                published = await self.event_bus.publish(trigger_event)
                if published:
                    greeting_results.append({
                        "section": prompt_data["section"],
                        "status": "published",
                    })
                else:
                    greeting_results.append({
                        "section": prompt_data["section"],
                        "status": "failed_to_publish",
                    })
            except Exception as e:
                greeting_results.append({
                    "section": prompt_data["section"],
                    "status": "error",
                    "error": str(e),
                })

        # Record session goals if provided
        if record_goals:
            # Note: goals will be set via update_session_goals API
            pass

        return {
            "ok": True,
            "session_id": self.current_session.session_id,
            "results": greeting_results,
            "last_session_context": bool(last_session_context),
            "repeat_goals_mode": repeat_goals,
        }

    def set_session_goals_sync(self, goals: str) -> dict:
        """Thread-safe sync wrapper to set session goals."""
        if not self.loop:
            return {"ok": False, "error": "Event loop unavailable"}

        future = asyncio.run_coroutine_threadsafe(
            self.set_session_goals(goals),
            self.loop,
        )
        try:
            return future.result(timeout=10)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def set_session_goals(self, goals: str) -> dict:
        """Set/update stream goals for current session."""
        if not self.current_session:
            return {"ok": False, "error": "No active session"}

        self.current_session.stream_goals = goals
        logger.info(f"Session goals set: {goals}")
        return {"ok": True, "session_id": self.current_session.session_id}

    def add_coaching_note_sync(self, category: str, description: str, severity: str = "info") -> dict:
        """Thread-safe sync wrapper to add a coaching note to session."""
        if not self.loop:
            return {"ok": False, "error": "Event loop unavailable"}

        future = asyncio.run_coroutine_threadsafe(
            self.add_coaching_note(category, description, severity),
            self.loop,
        )
        try:
            return future.result(timeout=10)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def add_coaching_note(self, category: str, description: str, severity: str = "info") -> dict:
        """Add a coaching note to the current session."""
        if not self.current_session:
            return {"ok": False, "error": "No active session"}

        from modules.session_history import SessionNote
        
        note = SessionNote(
            category=category,
            description=description,
            severity=severity,
        )
        self.current_session.coaching_notes.append(note)
        logger.info(f"Session note: {category} - {description}")
        return {"ok": True, "session_id": self.current_session.session_id}

    def get_session_status_sync(self) -> dict:
        """Get current session status (sync wrapper)."""
        if not self.current_session:
            return {"ok": False, "error": "No active session"}

        duration = time.time() - self.session_start_time
        return {
            "ok": True,
            "session_id": self.current_session.session_id,
            "started_at": self.current_session.started_at,
            "duration_seconds": duration,
            "channel": self.current_session.channel,
            "goals": self.current_session.stream_goals,
            "viewers_count": len(self.current_session.viewers),
            "views_list": [v.nick for v in self.current_session.viewers],
            "scene_to_mode_mapping": self.config.scene_to_mode_mapping,
            "training_mode": self.training_mode_active,
        }

    def toggle_training_mode_sync(self) -> dict:
        """Toggle training mode on/off (sync wrapper)."""
        if not self.loop:
            return {"ok": False, "error": "Event loop unavailable"}

        future = asyncio.run_coroutine_threadsafe(
            self.toggle_training_mode(),
            self.loop,
        )
        try:
            return future.result(timeout=10)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def toggle_training_mode(self) -> dict:
        """Toggle training mode on/off."""
        self.training_mode_active = not self.training_mode_active
        if self.current_session:
            self.current_session.training_mode = self.training_mode_active
        
        state = "ENABLED" if self.training_mode_active else "DISABLED"
        action = "Listening passively" if self.training_mode_active else "Real-time guidance active"
        logger.info(f"Training mode {state} - {action}")
        
        return {
            "ok": True,
            "training_mode": self.training_mode_active,
            "message": action,
        }

    def end_session_with_analysis_sync(self, user_notes: Optional[str] = None) -> dict:
        """End session and run analysis (sync wrapper)."""
        if not self.loop:
            return {"ok": False, "error": "Event loop unavailable"}

        future = asyncio.run_coroutine_threadsafe(
            self.end_session_with_analysis(user_notes),
            self.loop,
        )
        try:
            return future.result(timeout=30)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    async def end_session_with_analysis(self, user_notes: Optional[str] = None) -> dict:
        """End current session, run analysis, and save to history."""
        if not self.current_session:
            return {"ok": False, "error": "No active session"}

        # Mark session as ended
        self.current_session.ended_at = time.time()
        if user_notes:
            self.current_session.user_notes = user_notes

        logger.info(f"Ending session {self.current_session.session_id} and analyzing...")

        # Run analysis
        if self.stream_analyzer:
            analysis = self.stream_analyzer.analyze_session(self.current_session)
            logger.info(f"Session analysis complete: {len(self.current_session.key_insights)} insights")
        else:
            analysis = {"error": "Analyzer unavailable"}

        # Save to history
        if self.session_history_manager and self.config.training_mode_auto_save_on_end:
            self.session_history_manager.add_session(self.current_session)
            logger.info(f"Session saved to history")

        # Generate training report for next session
        training_focus = ""
        if self.stream_analyzer and self.current_session.analysis_report:
            training_focus = self.stream_analyzer.generate_training_report(self.current_session)

        # Start new session for next stream
        self.current_session = StreamSession(
            session_id=str(uuid.uuid4()),
            started_at=time.time(),
            channel=self.config.twitch_channel,
            training_mode=self.config.training_mode_enabled,
        )
        self.session_start_time = time.time()

        return {
            "ok": True,
            "session_duration_minutes": analysis.get("duration_minutes", 0),
            "insights_count": len(analysis.get("insights", [])),
            "analysis": analysis,
            "training_focus": training_focus,
        }

    def get_dock_status(self) -> dict:
        """Status payload for OBS dock UI."""
        bus_metrics = self.event_bus.get_metrics()

        safety_level = "unknown"
        if self.safety_manager:
            try:
                safety_stats = self.safety_manager.get_stats()
                safety_level = safety_stats.get("safety_level", "unknown")
            except Exception:
                pass

        chat_total_messages = 0
        if self.chat_reader:
            try:
                chat_stats = self.chat_reader.get_stats()
                chat_total_messages = chat_stats.get("total_messages", 0)
            except Exception:
                pass

        voice_total_chunks = 0
        if self.voice_analyzer:
            try:
                voice_stats = self.voice_analyzer.get_stats()
                voice_total_chunks = voice_stats.get("total_chunks", 0)
            except Exception:
                pass

        cooldowns = {"in_ear_remaining": 0.0, "teleprompter_remaining": 0.0}
        if self.guidance_router:
            cooldowns = self.guidance_router.get_lane_cooldowns()

        scene_connection_status = "disabled"
        scene_connection_error = ""
        watcher_diagnostics = {}
        if self.obs_scene_watcher:
            watcher_diagnostics = self.obs_scene_watcher.get_diagnostics()
            scene_connection_status = "connected" if self.obs_scene_watcher.connected else "disconnected"
            scene_connection_error = self.obs_scene_watcher.connection_error or ""

        self.scene_auto_coaching_active = self.scene_extensive_feedback_enabled
        if self.config.scene_auto_disable_on_disconnect and scene_connection_status == "disconnected":
            self.scene_auto_coaching_active = False

        return {
            "guidance_paused": self.guidance_paused,
            "training_mode_active": self.training_mode_active,
            "lane_in_ear_enabled": self.in_ear_lane_enabled,
            "lane_teleprompter_enabled": self.teleprompter_lane_enabled,
            "scene_extensive_feedback_enabled": self.scene_extensive_feedback_enabled,
            "scene_auto_coaching_active": self.scene_auto_coaching_active,
            "scene_mode": self.current_scene_mode,
            "scene_name": self.current_scene_name,
            "scene_match_pattern": self.current_scene_match_pattern,
            "scene_connection_status": scene_connection_status,
            "scene_connection_error": scene_connection_error,
            "scene_watcher": watcher_diagnostics,
            "pending_scene_guardrail": {
                "id": self.pending_scene_guardrail_id,
                "mode": self.pending_scene_guardrail_mode,
                "due_at": self.pending_scene_guardrail_due_at,
                "active": bool(self.pending_scene_guardrail_id),
            },
            "safety_level": safety_level,
            "safety_banner": self._build_safety_guard_banner(),
            "event_drop_rate": bus_metrics.get("drop_rate", 0.0),
            "chat_total_messages": chat_total_messages,
            "voice_total_chunks": voice_total_chunks,
            "cooldowns": cooldowns,
            "last_guidance": self.tts_server.get_latest_guidance() if self.tts_server else None,
            "recent_guidance": self.tts_server.get_recent_guidance(limit=5) if self.tts_server else [],
            "pinned_guidance": self.tts_server.get_pinned_guidance() if self.tts_server else [],
        }
    
    async def run_with_status(self, status_interval: int = 30) -> None:
        """Run with periodic status updates."""
        async def status_loop():
            while self.running:
                await asyncio.sleep(status_interval)
                
                if not self.running:
                    break
                
                # Print status
                logger.info("="*60)
                logger.info("STREAM PRODUCER STATUS")
                logger.info("="*60)
                
                # Event bus metrics
                bus_metrics = self.event_bus.get_metrics()
                logger.info(f"📊 Event Bus:")
                logger.info(f"  Published: {bus_metrics['published']}")
                logger.info(f"  Consumed: {bus_metrics['consumed']}")
                logger.info(f"  Dropped: {bus_metrics['dropped']}")
                logger.info(f"  Drop rate: {bus_metrics['drop_rate']:.1%}")
                
                # Safety metrics
                if not self.safety_manager:
                    continue
                safety_stats = self.safety_manager.get_stats()
                logger.info(f"🛡️  Safety:")
                logger.info(f"  Level: {safety_stats['safety_level']}")
                logger.info(f"  CPU: {safety_stats['headroom']['cpu_available']:.1f}% avail")
                logger.info(f"  RAM: {safety_stats['headroom']['memory_available']:.1f}% avail")
                
                # Chat stats
                if self.chat_reader:
                    chat_stats = self.chat_reader.get_stats()
                    logger.info(f"💬 Chat:")
                    logger.info(f"  Messages: {chat_stats['total_messages']}")
                    logger.info(f"  Users: {chat_stats['unique_users']}")
                
                # Voice stats
                if self.voice_analyzer:
                    voice_stats = self.voice_analyzer.get_stats()
                    logger.info(f"🎤 Voice:")
                    logger.info(f"  Chunks: {voice_stats['total_chunks']}")
                
                logger.info("="*60)
        
        # Run status updates in background
        status_task = asyncio.create_task(status_loop())
        
        # Wait for shutdown
        await self.start()
        
        # Cancel status task
        status_task.cancel()
        try:
            await status_task
        except asyncio.CancelledError:
            pass


async def async_main():
    """Async entry point."""
    parser = argparse.ArgumentParser(description="Twitch AI Stream Producer (Async)")
    parser.add_argument(
        "--status-interval",
        type=int,
        default=30,
        help="Status update interval in seconds (0 to disable)"
    )
    args = parser.parse_args()
    
    # Create producer
    producer = AsyncStreamProducer()
    
    # Initialize components (sync)
    producer._init_components()
    
    # Setup signal handlers
    def signal_handler(sig, frame):
        logger.info(f"\nReceived signal {sig}, shutting down...")
        producer.shutdown_event.set()
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    try:
        # Run with status updates
        if args.status_interval > 0:
            await producer.run_with_status(args.status_interval)
        else:
            await producer.start()
    finally:
        await producer.stop()


def main():
    """Entry point wrapper."""
    print("\n" + "="*60)
    print("TWITCH AI STREAM PRODUCER")
    print("="*60)
    print("Event-Driven Architecture (Phase 2a)")
    print("Version: 2.0.0-alpha")
    print("="*60 + "\n")
    
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
