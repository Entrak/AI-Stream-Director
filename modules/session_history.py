"""
Session History Manager - Tracks and persists stream session data

Handles:
- Loading previous session summaries
- Recording current session events (viewers, scene transitions, behavioral notes)
- Exporting session summaries for coaching context
"""

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class SessionViewer:
    """Record of a viewer in the session"""
    nick: str
    first_seen_at: float  # unix timestamp
    scene_when_joined: str  # which scene was active
    notes: Optional[str] = None


@dataclass
class SessionNote:
    """Behavioral or coaching note from a session"""
    category: str  # "filler_words", "pacing", "technical", "engagement", etc.
    description: str
    severity: str = "info"  # "info", "warning", "success"
    occurrence_count: int = 1


@dataclass
class StreamSession:
    """Complete session record"""
    session_id: str
    started_at: float  # unix timestamp
    ended_at: Optional[float] = None
    channel: str = ""
    
    # Goals and objectives
    stream_goals: Optional[str] = None
    stream_objectives: Optional[List[str]] = field(default_factory=list)
    
    # Viewer tracking
    viewers: List[SessionViewer] = field(default_factory=list)
    total_viewer_count: int = 0
    
    # Scene tracking
    scene_transitions: List[Dict[str, Any]] = field(default_factory=list)  # {ts, from_scene, to_scene, viewer_count}
    
    # Coaching notes
    coaching_notes: List[SessionNote] = field(default_factory=list)
    
    # Stats
    duration_minutes: float = 0.0
    peak_viewer_count: int = 0
    
    # User-provided feedback
    user_notes: str = ""
    
    # Training mode and metrics
    training_mode: bool = False  # If True, stream was in analysis-only mode
    
    # Voice metrics (aggregate across stream)
    voice_metrics: Dict[str, Any] = field(default_factory=dict)  # {avg_wpm, filler_count, energy_trend, clarity_notes}
    
    # Chat metrics (aggregate across stream)
    chat_metrics: Dict[str, Any] = field(default_factory=dict)  # {messages_per_min, peak_rate, engagement_score}
    
    # Scene performance (how each scene performed)
    scene_performance: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # {scene_name: {duration, viewers_at_end, retention}}
    
    # Post-stream analysis and recommendations
    analysis_report: Optional[str] = None
    
    # Key insights extracted from session
    key_insights: List[Dict[str, Any]] = field(default_factory=list)  # {insight_type, priority, description}

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        data = asdict(self)
        # Convert viewers to dicts
        data['viewers'] = [asdict(v) for v in self.viewers]
        # Convert notes to dicts
        data['coaching_notes'] = [asdict(n) for n in self.coaching_notes]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'StreamSession':
        """Create from dictionary"""
        # Convert viewer dicts to SessionViewer objects
        if 'viewers' in data and data['viewers']:
            data['viewers'] = [SessionViewer(**v) for v in data['viewers']]
        # Convert note dicts to SessionNote objects
        if 'coaching_notes' in data and data['coaching_notes']:
            data['coaching_notes'] = [SessionNote(**n) for n in data['coaching_notes']]
        return cls(**data)


class SessionHistoryManager:
    """Manages loading, saving, and parsing stream session history"""
    
    def __init__(self, history_path: str = "data/session_history.json"):
        self.history_path = Path(history_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.sessions: List[StreamSession] = self._load_history()
        logger.info(f"SessionHistoryManager initialized with {len(self.sessions)} previous sessions")
    
    def _load_history(self) -> List[StreamSession]:
        """Load all sessions from history file"""
        if self.history_path.exists():
            try:
                with open(self.history_path, 'r') as f:
                    data = json.load(f)
                sessions = [StreamSession.from_dict(s) for s in data.get('sessions', [])]
                logger.info(f"Loaded {len(sessions)} sessions from history")
                return sessions
            except Exception as e:
                logger.error(f"Error loading session history: {e}")
                return []
        return []
    
    def save_history(self) -> None:
        """Save all sessions to history file"""
        try:
            data = {
                'sessions': [s.to_dict() for s in self.sessions],
                'last_updated': datetime.now().isoformat()
            }
            with open(self.history_path, 'w') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved {len(self.sessions)} sessions to history")
        except Exception as e:
            logger.error(f"Error saving session history: {e}")
    
    def get_last_session(self) -> Optional[StreamSession]:
        """Get the most recent completed session"""
        if not self.sessions:
            return None
        # Return the last session (sessions are added chronologically)
        return self.sessions[-1]
    
    def get_session_summary(self, session: StreamSession) -> str:
        """Generate a readable summary of a session for coaching context"""
        if not session.ended_at:
            return "Session is currently active"
        
        lines = []
        lines.append(f"📊 Session Summary: {datetime.fromtimestamp(session.started_at).strftime('%Y-%m-%d %H:%M')}")
        lines.append(f"Duration: {session.duration_minutes:.1f} minutes")
        lines.append("")
        
        if session.viewers:
            lines.append(f"👥 Viewers: {len(session.viewers)} unique, peak {session.peak_viewer_count}")
            # Show first few viewers
            for viewer in session.viewers[:5]:
                joined_scene = viewer.scene_when_joined or "unknown"
                lines.append(f"  - {viewer.nick} (joined during: {joined_scene})")
            if len(session.viewers) > 5:
                lines.append(f"  ... and {len(session.viewers) - 5} more")
            lines.append("")
        
        if session.stream_goals:
            lines.append(f"🎯 Goals: {session.stream_goals}")
            lines.append("")
        
        if session.coaching_notes:
            lines.append("📝 Coaching Notes:")
            for note in session.coaching_notes:
                emoji = "✅" if note.severity == "success" else "⚠️" if note.severity == "warning" else "ℹ️"
                lines.append(f"  {emoji} {note.category}: {note.description}")
                if note.occurrence_count > 1:
                    lines.append(f"     (occurred {note.occurrence_count} times)")
            lines.append("")
        
        if session.user_notes:
            lines.append(f"💭 Notes: {session.user_notes}")
        
        return "\n".join(lines)
    
    def add_session(self, session: StreamSession) -> None:
        """Add a completed session to history"""
        self.sessions.append(session)
        self.save_history()
        logger.info(f"Added session {session.session_id} to history")
    
    def record_viewer_join(self, current_session: StreamSession, nick: str, scene: str) -> None:
        """Record a viewer joining in the current session"""
        # Check if viewer already exists
        for viewer in current_session.viewers:
            if viewer.nick.lower() == nick.lower():
                # Viewer already recorded, don't duplicate
                return
        
        viewer = SessionViewer(
            nick=nick,
            first_seen_at=datetime.now().timestamp(),
            scene_when_joined=scene
        )
        current_session.viewers.append(viewer)
        current_session.total_viewer_count = len(current_session.viewers)
    
    def record_scene_transition(
        self,
        current_session: StreamSession,
        from_scene: str,
        to_scene: str,
        viewer_count: int = 0
    ) -> None:
        """Record a scene transition in the current session"""
        transition = {
            'timestamp': datetime.now().timestamp(),
            'from_scene': from_scene,
            'to_scene': to_scene,
            'viewer_count': viewer_count
        }
        current_session.scene_transitions.append(transition)
        if viewer_count > current_session.peak_viewer_count:
            current_session.peak_viewer_count = viewer_count
    
    def add_coaching_note(
        self,
        current_session: StreamSession,
        category: str,
        description: str,
        severity: str = "info",
        occurrence_count: int = 1
    ) -> None:
        """Add a coaching note to the current session"""
        # Check if similar note exists and update count
        for note in current_session.coaching_notes:
            if note.category == category and note.description == description:
                note.occurrence_count += occurrence_count
                return
        
        # New note
        note = SessionNote(
            category=category,
            description=description,
            severity=severity,
            occurrence_count=occurrence_count
        )
        current_session.coaching_notes.append(note)
    
    def format_last_session_context(self) -> str:
        """Format the last session as coaching context for the next kickoff"""
        last_session = self.get_last_session()
        if not last_session:
            return ""
        
        return self.get_session_summary(last_session)
