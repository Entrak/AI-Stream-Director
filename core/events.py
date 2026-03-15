"""
Core event contracts for MVP dual-lane guidance routing.
"""

from dataclasses import dataclass, field
from typing import List, Literal
import time
import uuid

Priority = Literal["critical", "high", "normal", "low"]


@dataclass
class ChatSnapshot:
    recent_message_count: int
    total_messages: int
    new_users: List[str] = field(default_factory=list)


@dataclass
class VoiceSnapshot:
    words_per_minute: float
    filler_count: int
    energy_level: float


@dataclass
class GuidanceDecision:
    text: str
    priority: Priority
    send_in_ear: bool
    send_teleprompter: bool
    reason: str
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
