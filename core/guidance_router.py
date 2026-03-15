"""
Guidance routing policy for MVP dual-lane delivery.
"""

import time
from typing import Optional

from config.config import AppConfig
from core.events import GuidanceDecision, ChatSnapshot, VoiceSnapshot, Priority


class GuidanceRouter:
    """
    Routes generated guidance into:
    - in-ear lane (TTS)
    - teleprompter lane (visual)

    Uses simple priority + lane cooldown policy for MVP.
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self._last_in_ear = 0.0
        self._last_teleprompter = 0.0

    def _classify_priority(self, chat: ChatSnapshot, voice: VoiceSnapshot) -> Priority:
        if chat.new_users:
            return "high"

        if voice.words_per_minute > max(self.config.words_per_min_max + 30, 250):
            return "critical"

        if voice.filler_count > self.config.max_filler_count_per_min + 5:
            return "high"

        if chat.recent_message_count < self.config.chat_slow_threshold:
            return "normal"

        return "low"

    def _in_ear_text(self, text: str) -> str:
        words = text.split()
        if len(words) <= 18:
            return text
        return " ".join(words[:18]) + "..."

    def get_lane_cooldowns(self) -> dict[str, float]:
        """Return remaining cooldown seconds for each guidance lane."""
        now = time.time()
        in_ear_remaining = max(0.0, self.config.in_ear_cooldown - (now - self._last_in_ear))
        teleprompter_remaining = max(0.0, self.config.teleprompter_cooldown - (now - self._last_teleprompter))
        return {
            "in_ear_remaining": in_ear_remaining,
            "teleprompter_remaining": teleprompter_remaining,
        }

    def route(
        self,
        feedback_text: str,
        chat: ChatSnapshot,
        voice: VoiceSnapshot
    ) -> Optional[GuidanceDecision]:
        if not feedback_text:
            return None

        now = time.time()
        priority = self._classify_priority(chat, voice)

        send_in_ear = False
        send_teleprompter = False

        if self.config.in_ear_enabled:
            in_ear_due = (now - self._last_in_ear) >= self.config.in_ear_cooldown
            if priority in ["critical", "high"] and in_ear_due:
                send_in_ear = True
            elif priority == "normal" and in_ear_due and chat.recent_message_count < self.config.chat_slow_threshold:
                send_in_ear = True

        if self.config.teleprompter_enabled:
            tele_due = (now - self._last_teleprompter) >= self.config.teleprompter_cooldown
            if priority in ["critical", "high", "normal"] and tele_due:
                send_teleprompter = True

        if not send_in_ear and not send_teleprompter:
            return None

        if send_in_ear:
            self._last_in_ear = now

        if send_teleprompter:
            self._last_teleprompter = now

        final_text = self._in_ear_text(feedback_text) if send_in_ear and not send_teleprompter else feedback_text

        reason = f"priority={priority}, chat={chat.recent_message_count}, wpm={voice.words_per_minute:.0f}, fillers={voice.filler_count}"

        return GuidanceDecision(
            text=final_text,
            priority=priority,
            send_in_ear=send_in_ear,
            send_teleprompter=send_teleprompter,
            reason=reason,
        )
