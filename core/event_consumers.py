"""
Event Consumers - Handle events from the event bus and trigger actions.

Consumers:
- GuidanceTriggerConsumer: Aggregates chat/voice events, decides when to trigger AI guidance
- InferenceConsumer: Handles GUIDANCE_TRIGGERED events, generates AI feedback
- DeliveryConsumer: Handles INFERENCE_COMPLETE events, delivers via TTS/teleprompter
"""

import asyncio
import logging
import time
from collections import deque
from typing import Optional, List, Dict, Any, Callable

from core.event_bus import Event, EventType, EventPriority, get_event_bus
from core.events import ChatSnapshot, VoiceSnapshot, GuidanceDecision
from core.guidance_router import GuidanceRouter
from modules.adaptive_inference_router import AdaptiveInferenceRouter
from modules.tts_server import TTSServer
from config.config import AppConfig

logger = logging.getLogger(__name__)


class GuidanceTriggerConsumer:
    """
    Aggregates chat and voice events to determine when AI guidance should be generated.
    
    Replaces the polling-based ai_producer.should_trigger() logic with event-driven aggregation.
    """
    
    def __init__(self, config: AppConfig):
        self.config = config
        self.event_bus = get_event_bus()
        
        # Event aggregation buffers
        self.chat_messages: deque = deque(maxlen=100)
        self.new_users: List[str] = []
        self.known_users: set = set()
        self.voice_metrics: Optional[Dict[str, Any]] = None
        self.last_voice_timestamp = 0.0
        
        # Trigger timing
        self.last_trigger_time = 0.0
        self.min_interval = config.ai_processing_interval  # Minimum time between triggers
        
        # Subscribe to events
        self.event_bus.subscribe(EventType.CHAT_MESSAGE, self._on_chat_message)
        self.event_bus.subscribe(EventType.TRANSCRIPTION_COMPLETE, self._on_transcription)
        
        logger.info("GuidanceTriggerConsumer initialized")
    
    async def _on_chat_message(self, event: Event) -> None:
        """Handle incoming chat messages."""
        try:
            username = event.data.get("username", "")
            message = event.data.get("message", "")
            timestamp = event.data.get("timestamp", time.time())
            
            # Track new users
            if username not in self.known_users:
                self.known_users.add(username)
                self.new_users.append(username)
            
            # Store message
            self.chat_messages.append({
                "username": username,
                "message": message,
                "timestamp": timestamp,
            })
            
            # Check if we should trigger guidance
            await self._check_trigger()
            
        except Exception as e:
            logger.error(f"Error handling chat message event: {e}")
    
    async def _on_transcription(self, event: Event) -> None:
        """Handle voice transcription events."""
        try:
            self.voice_metrics = {
                "words_per_minute": event.data.get("words_per_minute", 0.0),
                "filler_count": event.data.get("filler_count", 0),
                "total_words": event.data.get("total_words", 0),
                "energy_level": event.data.get("energy_level", 0.0),
                "transcript": event.data.get("transcript", ""),
            }
            self.last_voice_timestamp = event.data.get("timestamp", time.time())
            
            # Check if we should trigger guidance
            await self._check_trigger()
            
        except Exception as e:
            logger.error(f"Error handling transcription event: {e}")
    
    async def _check_trigger(self) -> None:
        """Determine if guidance should be triggered based on accumulated events."""
        try:
            # Respect minimum interval
            now = time.time()
            if now - self.last_trigger_time < self.min_interval:
                return
            
            # Build snapshots
            chat_snapshot = self._build_chat_snapshot()
            voice_snapshot = self._build_voice_snapshot()
            
            # Trigger conditions (same logic as original ai_producer.should_trigger)
            should_trigger = False
            trigger_reason = None
            
            # Check chat activity
            recent_30s = sum(1 for msg in self.chat_messages if now - msg["timestamp"] < 30.0)
            if len(self.new_users) > 0:
                should_trigger = True
                trigger_reason = f"New viewers ({len(self.new_users)})"
            elif recent_30s == 0:
                should_trigger = True
                trigger_reason = "Chat slow (0 msgs)"
            elif recent_30s > 10:
                should_trigger = True
                trigger_reason = f"Chat active ({recent_30s} msgs)"
            
            # Check voice metrics
            if self.voice_metrics and self.voice_metrics.get("filler_count", 0) > 5:
                should_trigger = True
                trigger_reason = f"Filler words ({self.voice_metrics['filler_count']})"
            
            # Emit guidance trigger event
            if should_trigger:
                logger.info(f"Triggering guidance: {trigger_reason}")
                
                # Create GUIDANCE_TRIGGERED event
                trigger_event = Event(
                    type=EventType.GUIDANCE_TRIGGERED,
                    priority=EventPriority.NORMAL,
                    data={
                        "reason": trigger_reason,
                        "chat_snapshot": {
                            "recent_message_count": recent_30s,
                            "total_messages": len(self.chat_messages),
                            "new_users": self.new_users.copy(),
                            "recent_messages": list(self.chat_messages)[-10:],  # Last 10 messages
                        },
                        "voice_snapshot": {
                            "words_per_minute": self.voice_metrics.get("words_per_minute", 0.0) if self.voice_metrics else 0.0,
                            "filler_count": self.voice_metrics.get("filler_count", 0) if self.voice_metrics else 0,
                            "energy_level": self.voice_metrics.get("energy_level", 0.0) if self.voice_metrics else 0.0,
                        },
                    },
                    source="guidance_trigger_consumer"
                )
                
                await self.event_bus.publish(trigger_event)
                
                # Update state
                self.last_trigger_time = now
                self.new_users.clear()
            
        except Exception as e:
            logger.error(f"Error checking trigger: {e}")
    
    def _build_chat_snapshot(self) -> ChatSnapshot:
        """Build ChatSnapshot from current state."""
        now = time.time()
        recent_30s = sum(1 for msg in self.chat_messages if now - msg["timestamp"] < 30.0)
        
        return ChatSnapshot(
            recent_message_count=recent_30s,
            total_messages=len(self.chat_messages),
            new_users=self.new_users.copy(),
        )
    
    def _build_voice_snapshot(self) -> VoiceSnapshot:
        """Build VoiceSnapshot from current state."""
        if not self.voice_metrics:
            return VoiceSnapshot(
                words_per_minute=0.0,
                filler_count=0,
                energy_level=0.0,
            )
        
        return VoiceSnapshot(
            words_per_minute=self.voice_metrics.get("words_per_minute", 0.0),
            filler_count=self.voice_metrics.get("filler_count", 0),
            energy_level=self.voice_metrics.get("energy_level", 0.0),
        )


class InferenceConsumer:
    """
    Handles GUIDANCE_TRIGGERED events and generates AI feedback through the inference router.
    
    Checks safety before inference and emits INFERENCE_COMPLETE or INFERENCE_FAILED events.
    """
    
    def __init__(self, inference_router: AdaptiveInferenceRouter, config: Optional[AppConfig] = None):
        self.inference_router = inference_router
        self.config = config
        self.event_bus = get_event_bus()
        
        # Subscribe to trigger events
        self.event_bus.subscribe(EventType.GUIDANCE_TRIGGERED, self._on_guidance_triggered)
        
        logger.info("InferenceConsumer initialized")
    
    async def _on_guidance_triggered(self, event: Event) -> None:
        """Generate AI guidance when triggered."""
        try:
            reason = event.data.get("reason", "unknown")
            chat_data = event.data.get("chat_snapshot", {})
            voice_data = event.data.get("voice_snapshot", {})
            mode = event.data.get("mode", "normal")
            intent = event.data.get("intent", "general")
            scene_mode = event.data.get("scene_mode", "normal")
            focus_goal = event.data.get("focus_goal", "general")
            
            # Check if this is a session kickoff event
            session_kickoff_section = event.data.get("session_kickoff_section")
            session_kickoff_template = event.data.get("session_kickoff_template")
            session_kickoff_instruction = event.data.get("session_kickoff_instruction")
            
            logger.info(f"Generating guidance: {reason}")
            
            # Build prompt (simplified version of ai_producer._build_prompt)
            prompt = self._build_prompt(chat_data, voice_data, reason, mode, intent, scene_mode, focus_goal)

            system_prompt = (
                "You are an AI stream producer for a Twitch streamer. "
                "Give 1-2 actionable, encouraging tips in under 50 words. "
                "Be specific and concise."
            )

            # Handle session kickoff sections
            if session_kickoff_section:
                if session_kickoff_section == "greeting":
                    system_prompt = (
                        "You are a warm and enthusiastic stream producer coach greeting the streamer at the start of their stream. "
                        f"{session_kickoff_instruction} "
                        "Keep it brief (50-70 words), genuine, and energetic."
                    )
                elif session_kickoff_section == "goals_question":
                    system_prompt = (
                        "You are a supportive stream producer coach. "
                        f"{session_kickoff_instruction} "
                        "Keep it conversational and encouraging (40-60 words)."
                    )
                elif session_kickoff_section == "pep_talk":
                    system_prompt = (
                        "You are an elite stream producer giving a motivational pep talk. "
                        f"{session_kickoff_instruction} "
                        "Include practical tips, confidence boost, and a reminder of what's important. "
                        "Keep it within 80-120 words."
                    )
                prompt = session_kickoff_instruction or "Help me prepare for the stream"  # Use the instruction as the prompt for kickoff sections

            elif mode == "extensive":
                if scene_mode == "starting" or "starting" in intent:
                    template = self.config.starting_scene_template if self.config else ""
                    system_prompt = (
                        "You are an elite stream producer coach. "
                        f"{template} "
                        "Keep it focused, warm, and actionable in 90-130 words."
                    )
                elif scene_mode == "brb" or "brb" in intent:
                    template = self.config.brb_scene_template if self.config else ""
                    system_prompt = (
                        "You are an elite stream producer coach doing a focused mid-stream review during BRB. "
                        f"{template} "
                        "Keep it practical and direct in 100-140 words."
                    )
                else:
                    system_prompt = (
                        "You are an elite stream producer coach. "
                        "Provide focused, in-depth guidance with prioritized actions and one clarifying question "
                        "to align stream goals. Keep it within 90-130 words."
                    )
            
            # Ensure prompt is always a string
            if not prompt:
                prompt = reason or "Help me improve my stream"
            
            # Generate guidance through router (async)
            response = await asyncio.get_event_loop().run_in_executor(
                None,
                self.inference_router.generate_guidance,
                prompt,
                system_prompt,
                {"chat": chat_data, "voice": voice_data}
            )
            
            if response and response.error is None:
                # Inference succeeded
                logger.info(f"Inference complete via {response.provider}")
                
                complete_event = Event(
                    type=EventType.INFERENCE_COMPLETE,
                    priority=EventPriority.NORMAL,
                    data={
                        "text": response.text,
                        "provider": response.provider,
                        "reason": reason,
                        "chat_snapshot": chat_data,
                        "voice_snapshot": voice_data,
                        "session_kickoff_section": session_kickoff_section,
                    },
                    correlation_id=event.correlation_id,
                    source="inference_consumer"
                )
                await self.event_bus.publish(complete_event)
                
            elif response is None:
                # Router skipped inference (safety)
                logger.info("Inference skipped to protect stream resources")
                
                failed_event = Event(
                    type=EventType.INFERENCE_FAILED,
                    priority=EventPriority.LOW,
                    data={
                        "error": "Skipped due to safety constraints",
                        "reason": reason,
                    },
                    correlation_id=event.correlation_id,
                    source="inference_consumer"
                )
                await self.event_bus.publish(failed_event)
                
            else:
                # Inference failed
                logger.warning(f"Inference failed: {response.error}")
                
                failed_event = Event(
                    type=EventType.INFERENCE_FAILED,
                    priority=EventPriority.LOW,
                    data={
                        "error": response.error,
                        "reason": reason,
                    },
                    correlation_id=event.correlation_id,
                    source="inference_consumer"
                )
                await self.event_bus.publish(failed_event)
            
        except Exception as e:
            logger.error(f"Error in inference consumer: {e}", exc_info=True)
    
    def _build_prompt(
        self,
        chat_data: Dict,
        voice_data: Dict,
        reason: str,
        mode: str = "normal",
        intent: str = "general",
        scene_mode: str = "normal",
        focus_goal: str = "general",
    ) -> str:
        """Build prompt from chat and voice data."""
        recent_messages = chat_data.get("recent_messages", [])
        recent_msg_count = chat_data.get("recent_message_count", 0)
        new_users = chat_data.get("new_users", [])
        
        wpm = voice_data.get("words_per_minute", 0.0)
        filler_count = voice_data.get("filler_count", 0)
        
        prompt_parts = [
            f"Trigger reason: {reason}",
            f"Mode: {mode}",
            f"Intent: {intent}",
            f"Scene mode: {scene_mode}",
            f"Focus goal: {focus_goal}",
            f"Chat activity (last 30s): {recent_msg_count} messages",
        ]
        
        if new_users:
            prompt_parts.append(f"New viewers: {', '.join(new_users[:5])}")
        
        if recent_messages:
            prompt_parts.append(f"Recent chat:")
            for msg in recent_messages[-5:]:  # Last 5 messages
                prompt_parts.append(f"  {msg.get('username', 'Unknown')}: {msg.get('message', '')}")
        
        if wpm > 0:
            prompt_parts.append(f"Speaking pace: {wpm:.0f} words/min")
        
        if filler_count > 0:
            prompt_parts.append(f"Filler words detected: {filler_count}")
        
        if mode == "extensive":
            prompt_parts.append("\nProvide extensive feedback with concrete priorities and next steps:")
        else:
            prompt_parts.append("\nProvide brief, actionable feedback:")
        
        return "\n".join(prompt_parts)


class DeliveryConsumer:
    """
    Handles INFERENCE_COMPLETE events and delivers guidance via TTS/teleprompter.
    
    Uses GuidanceRouter to determine delivery method (in-ear, teleprompter, or both).
    """
    
    def __init__(
        self,
        tts_server: TTSServer,
        guidance_router: GuidanceRouter,
        should_deliver: Optional[Callable[[], bool]] = None,
        lane_state_provider: Optional[Callable[[], tuple[bool, bool]]] = None,
    ):
        self.tts_server = tts_server
        self.guidance_router = guidance_router
        self.should_deliver = should_deliver
        self.lane_state_provider = lane_state_provider
        self.event_bus = get_event_bus()
        
        # Subscribe to inference complete events
        self.event_bus.subscribe(EventType.INFERENCE_COMPLETE, self._on_inference_complete)
        
        logger.info("DeliveryConsumer initialized")
    
    async def _on_inference_complete(self, event: Event) -> None:
        """Deliver AI guidance when inference completes."""
        try:
            if self.should_deliver is not None and not self.should_deliver():
                logger.info("Guidance delivery paused by operator control")
                return

            text = event.data.get("text", "")
            provider = event.data.get("provider", "unknown")
            chat_snapshot = event.data.get("chat_snapshot", {})
            voice_snapshot = event.data.get("voice_snapshot", {})
            
            if not text:
                logger.warning("Empty guidance text, skipping delivery")
                return
            
            # Build snapshots for guidance router
            chat_snap = ChatSnapshot(
                recent_message_count=chat_snapshot.get("recent_message_count", 0),
                total_messages=chat_snapshot.get("total_messages", 0),
                new_users=chat_snapshot.get("new_users", []),
            )
            
            voice_snap = VoiceSnapshot(
                words_per_minute=voice_snapshot.get("words_per_minute", 0.0),
                filler_count=voice_snapshot.get("filler_count", 0),
                energy_level=voice_snapshot.get("energy_level", 0.0),
            )
            
            # Determine delivery method
            decision = self.guidance_router.route(text, chat_snap, voice_snap)
            
            if not decision:
                logger.debug("Guidance router skipped delivery due to lane cooldown policy")
                return

            if self.lane_state_provider is not None:
                lane_in_ear_enabled, lane_teleprompter_enabled = self.lane_state_provider()
                if not lane_in_ear_enabled:
                    decision.send_in_ear = False
                if not lane_teleprompter_enabled:
                    decision.send_teleprompter = False

                if not decision.send_in_ear and not decision.send_teleprompter:
                    logger.info("Guidance dropped because both delivery lanes are disabled")
                    return
            
            delivered = False
            
            # Deliver to teleprompter if requested
            if decision.send_teleprompter:
                card_id = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self.tts_server.publish_teleprompter,
                    decision.text,
                    decision.priority
                )
                if card_id:
                    delivered = True
                    logger.info(f"Published teleprompter card: {card_id}")
            
            # Generate TTS audio if requested
            if decision.send_in_ear:
                audio_file = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self.tts_server.generate_audio,
                    decision.text
                )
                if audio_file:
                    delivered = True
                    logger.info(f"Generated TTS audio: {audio_file}")
                else:
                    logger.error("Failed to generate TTS audio")
            
            if delivered:
                self.tts_server.record_latest_guidance(
                    text=decision.text,
                    priority=decision.priority,
                    provider=provider,
                    reason=decision.reason,
                    send_in_ear=decision.send_in_ear,
                    send_teleprompter=decision.send_teleprompter,
                )
                logger.info(f"✓ Guidance delivered ({decision.priority}): {decision.reason}")
                
                # Emit delivery complete event
                delivery_event = Event(
                    type=EventType.TTS_COMPLETE,
                    priority=EventPriority.LOW,
                    data={
                        "decision": {
                            "text": decision.text,
                            "priority": decision.priority,
                            "send_in_ear": decision.send_in_ear,
                            "send_teleprompter": decision.send_teleprompter,
                            "reason": decision.reason,
                        },
                        "provider": provider,
                    },
                    correlation_id=event.correlation_id,
                    source="delivery_consumer"
                )
                await self.event_bus.publish(delivery_event)
            
        except Exception as e:
            logger.error(f"Error in delivery consumer: {e}", exc_info=True)
