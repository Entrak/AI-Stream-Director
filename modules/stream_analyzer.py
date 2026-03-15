"""
Stream Analyzer - Post-session analysis and insights generation

Analyzes collected session data to extract:
- Voice patterns (pacing, filler words, energy trends)
- Chat engagement patterns
- Scene performance (viewer retention, transitions)
- Technical observations
- Personalized recommendations for next stream
"""

import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
from modules.session_history import StreamSession, SessionNote

logger = logging.getLogger(__name__)


class StreamAnalyzer:
    """Analyzes completed stream sessions to generate insights and recommendations"""
    
    def __init__(self):
        pass
    
    def analyze_session(self, session: StreamSession) -> Dict[str, Any]:
        """
        Comprehensive analysis of a completed stream session.
        
        Returns dict with:
        - summary: human-readable summary
        - insights: list of key insights
        - metrics_analysis: detailed metrics breakdown
        - recommendations: actionable recommendations for next stream
        """
        if not session.ended_at or not session.started_at:
            return {"error": "Session not properly ended"}
        
        duration = session.ended_at - session.started_at
        session.duration_minutes = duration / 60.0
        
        # Analyze different aspects
        voice_analysis = self._analyze_voice_metrics(session)
        chat_analysis = self._analyze_chat_metrics(session)
        scene_analysis = self._analyze_scene_performance(session)
        viewer_analysis = self._analyze_viewer_patterns(session)
        
        # Generate insights
        insights = self._generate_insights(
            session,
            voice_analysis,
            chat_analysis,
            scene_analysis,
            viewer_analysis
        )
        
        # Store in session
        session.key_insights = insights
        
        # Generate full report
        report = self._generate_report(
            session,
            duration,
            voice_analysis,
            chat_analysis,
            scene_analysis,
            viewer_analysis,
            insights
        )
        
        session.analysis_report = report
        
        return {
            "session_id": session.session_id,
            "duration_minutes": session.duration_minutes,
            "peaks": {
                "peak_viewers": session.peak_viewer_count,
                "total_unique_viewers": len(session.viewers),
            },
            "voice": voice_analysis,
            "chat": chat_analysis,
            "scenes": scene_analysis,
            "viewers": viewer_analysis,
            "insights": [self._format_insight(i) for i in insights],
            "report": report,
        }
    
    def _analyze_voice_metrics(self, session: StreamSession) -> Dict[str, Any]:
        """Analyze voice patterns from collected metrics"""
        metrics = session.voice_metrics or {}
        
        analysis = {
            "avg_words_per_minute": metrics.get("avg_wpm", 0),
            "total_filler_words": metrics.get("filler_count", 0),
            "filler_intensity": "high" if metrics.get("filler_count", 0) > 50 else (
                "medium" if metrics.get("filler_count", 0) > 20 else "low"
            ),
            "energy_trend": metrics.get("energy_trend", "stable"),
            "clarity_score": metrics.get("clarity_score", 0),
            "observations": [],
        }
        
        # Generate observations
        avg_wpm = metrics.get("avg_wpm", 0)
        if avg_wpm < 100:
            analysis["observations"].append("Slower-than-typical pacing; consider picking up pace slightly")
        elif avg_wpm > 200:
            analysis["observations"].append("Fast-paced delivery; might want to slow down for clarity")
        
        filler_count = metrics.get("filler_count", 0)
        if filler_count > 50:
            analysis["observations"].append("High filler word usage; focus on replacing 'um', 'uh', 'like' with silence or strategic pauses")
        
        return analysis
    
    def _analyze_chat_metrics(self, session: StreamSession) -> Dict[str, Any]:
        """Analyze chat engagement patterns"""
        metrics = session.chat_metrics or {}
        
        analysis = {
            "total_messages": metrics.get("total_messages", 0),
            "avg_messages_per_minute": metrics.get("messages_per_min", 0),
            "peak_message_rate": metrics.get("peak_rate", 0),
            "engagement_score": metrics.get("engagement_score", 50),
            "observations": [],
        }
        
        # Generate observations
        msg_per_min = metrics.get("messages_per_min", 0)
        if msg_per_min < 1:
            analysis["observations"].append("Low chat engagement; consider more interactive segments or call-to-action moments")
        elif msg_per_min > 10:
            analysis["observations"].append("Excellent chat engagement! Keep doing what generates interaction")
        
        return analysis
    
    def _analyze_scene_performance(self, session: StreamSession) -> Dict[str, Any]:
        """Analyze how well each scene performed (viewer retention, transitions)"""
        performance = session.scene_performance or {}
        
        analysis = {
            "scenes_visited": len(performance),
            "scene_details": {},
            "best_performer": None,
            "worst_performer": None,
        }
        
        if not performance:
            return analysis
        
        # Analyze each scene
        best_retention = -1
        worst_retention = 101
        
        for scene_name, data in performance.items():
            duration = data.get("duration_seconds", 0)
            retention = data.get("retention_rate", 0)
            viewers_at_end = data.get("viewers_at_end", 0)
            
            analysis["scene_details"][scene_name] = {
                "duration_minutes": duration / 60.0,
                "viewers_at_end": viewers_at_end,
                "retention_rate": retention,
                "assessment": "good" if retention > 80 else ("fair" if retention > 60 else "needs improvement")
            }
            
            if retention > best_retention:
                best_retention = retention
                analysis["best_performer"] = scene_name
            
            if retention < worst_retention:
                worst_retention = retention
                analysis["worst_performer"] = scene_name
        
        return analysis
    
    def _analyze_viewer_patterns(self, session: StreamSession) -> Dict[str, Any]:
        """Analyze viewer join/leave patterns"""
        viewers = session.viewers or []
        transitions = session.scene_transitions or []
        
        analysis = {
            "total_unique_viewers": len(viewers),
            "peak_concurrent": session.peak_viewer_count,
            "returning_viewers": 0,
            "steady_viewers": 0,
            "join_patterns": {},
            "observations": [],
        }
        
        # Track which scenes viewers joined during
        for viewer in viewers:
            scene = viewer.scene_when_joined
            if scene:
                if scene not in analysis["join_patterns"]:
                    analysis["join_patterns"][scene] = 0
                analysis["join_patterns"][scene] += 1
        
        # Identify strong entry points
        if analysis["join_patterns"]:
            best_scene = max(analysis["join_patterns"], key=analysis["join_patterns"].get)
            analysis["observations"].append(f"Most viewers joined during '{best_scene}' scene - strong entry point!")
        
        return analysis
    
    def _generate_insights(
        self,
        session: StreamSession,
        voice: Dict[str, Any],
        chat: Dict[str, Any],
        scenes: Dict[str, Any],
        viewers: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Generate key insights from analysis"""
        insights = []
        
        # Voice insights
        if voice["filler_intensity"] == "high":
            insights.append({
                "insight_type": "voice",
                "priority": "high",
                "description": "High filler word usage detected. Replacing 'um', 'uh', 'like' with silence creates a more professional sound.",
            })
        
        if voice["avg_words_per_minute"] > 200:
            insights.append({
                "insight_type": "pacing",
                "priority": "medium",
                "description": "Speaking quite fast. Slowing down slightly would improve clarity and give viewers time to absorb information.",
            })
        
        # Chat insights
        if chat["engagement_score"] < 40:
            insights.append({
                "insight_type": "engagement",
                "priority": "high",
                "description": "Low chat engagement this stream. Try more questions, polls, or interactive segments next time.",
            })
        
        # Viewer insights
        if viewers["total_unique_viewers"] > 0 and viewers["peak_concurrent"] > 0:
            peak_vs_unique = (viewers["peak_concurrent"] / max(viewers["total_unique_viewers"], 1)) * 100
            if peak_vs_unique < 30:
                insights.append({
                    "insight_type": "retention",
                    "priority": "medium",
                    "description": "Viewer retention could improve. Peak concurrent viewers were much lower than unique visitors.",
                })
        
        # Scene insights
        if scenes["worst_performer"] and scenes["scene_details"]:
            worst_data = scenes["scene_details"].get(scenes["worst_performer"], {})
            if worst_data.get("retention_rate", 0) < 50:
                insights.append({
                    "insight_type": "scene",
                    "priority": "medium",
                    "description": f"'{scenes['worst_performer']}' scene had low viewer retention. Consider what happened during that scene and adjust the content or timing.",
                })
        
        # Positive insights
        if chat["engagement_score"] > 70:
            insights.append({
                "insight_type": "strength",
                "priority": "info",
                "description": "Excellent chat engagement! Keep up the interactive segments.",
            })
        
        if viewers["peak_concurrent"] > 10:
            insights.append({
                "insight_type": "strength",
                "priority": "info",
                "description": f"Great viewership this stream! {viewers['peak_concurrent']} concurrent viewers at peak.",
            })
        
        return insights
    
    def _format_insight(self, insight: Dict[str, Any]) -> str:
        """Format an insight for display"""
        priority_emoji = {
            "high": "🔴",
            "medium": "🟡",
            "info": "ℹ️",
            "success": "✅",
        }
        emoji = priority_emoji.get(insight.get("priority"), "•")
        return f"{emoji} {insight.get('insight_type', 'note').title()}: {insight.get('description', '')}"
    
    def _generate_report(
        self,
        session: StreamSession,
        duration: float,
        voice: Dict[str, Any],
        chat: Dict[str, Any],
        scenes: Dict[str, Any],
        viewers: Dict[str, Any],
        insights: List[Dict[str, Any]],
    ) -> str:
        """Generate comprehensive post-stream report"""
        lines = []
        
        lines.append("=" * 70)
        lines.append("📊 STREAM ANALYSIS REPORT")
        lines.append("=" * 70)
        lines.append("")
        
        # Session overview
        lines.append(f"📅 Date: {datetime.fromtimestamp(session.started_at).strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"⏱️  Duration: {session.duration_minutes:.1f} minutes")
        lines.append(f"📹 Channel: {session.channel}")
        if session.stream_goals:
            lines.append(f"🎯 Goals: {session.stream_goals}")
        lines.append("")
        
        # Key metrics
        lines.append("📈 KEY METRICS")
        lines.append("-" * 70)
        lines.append(f"👥 Viewers: {viewers['total_unique_viewers']} unique, {viewers['peak_concurrent']} peak concurrent")
        lines.append(f"💬 Chat: {chat['total_messages']} messages ({chat['avg_messages_per_minute']:.1f}/min avg)")
        lines.append(f"🎤 Voice: {voice['avg_words_per_minute']:.0f} WPM, {voice['total_filler_words']} filler words")
        lines.append(f"   Energy: {voice['energy_trend']} | Clarity: {voice['clarity_score']:.0f}%")
        lines.append("")
        
        # Scenes breakdown
        if scenes["scene_details"]:
            lines.append("🎬 SCENE PERFORMANCE")
            lines.append("-" * 70)
            for scene, data in scenes["scene_details"].items():
                lines.append(f"'{scene}': {data['duration_minutes']:.1f}m, {data['retention_rate']:.0f}% retention, {data['viewers_at_end']} viewers")
            lines.append("")
        
        # Insights
        lines.append("💡 KEY INSIGHTS & RECOMMENDATIONS")
        lines.append("-" * 70)
        for insight in insights:
            formatted = self._format_insight(insight)
            lines.append(f"  {formatted}")
        lines.append("")
        
        # Voice-specific feedback
        if voice["observations"]:
            lines.append("🎤 VOICE FEEDBACK")
            lines.append("-" * 70)
            for obs in voice["observations"]:
                lines.append(f"  • {obs}")
            lines.append("")
        
        # Chat feedback
        if chat["observations"]:
            lines.append("💬 ENGAGEMENT FEEDBACK")
            lines.append("-" * 70)
            for obs in chat["observations"]:
                lines.append(f"  • {obs}")
            lines.append("")
        
        # Closing
        lines.append("=" * 70)
        lines.append("💪 Great work on your stream! Use these insights to focus your coaching for the next session.")
        lines.append("=" * 70)
        
        return "\n".join(lines)
    
    def generate_training_report(self, session: StreamSession) -> str:
        """
        Generate a summarized training report highlighting what to work on.
        Used to inform next session's coaching.
        """
        if not session.analysis_report:
            return "No analysis available"
        
        # Extract key focus areas from analysis
        focus_areas = []
        
        for insight in session.key_insights:
            if insight.get("priority") in ("high", "medium"):
                focus_areas.append({
                    "type": insight.get("insight_type"),
                    "description": insight.get("description"),
                    "priority": insight.get("priority"),
                })
        
        lines = []
        lines.append("🎯 TRAINING FOCUS AREAS FOR NEXT STREAM")
        lines.append("=" * 60)
        
        if not focus_areas:
            lines.append("Great work! No major areas to focus on. Keep doing what you're doing!")
            return "\n".join(lines)
        
        for area in focus_areas:
            priority_emoji = "🔴" if area["priority"] == "high" else "🟡"
            lines.append(f"\n{priority_emoji} {area['type'].title()}")
            lines.append(f"   {area['description']}")
        
        lines.append("\n" + "=" * 60)
        lines.append("When you're ready for next stream, we'll focus coaching on these areas!")
        
        return "\n".join(lines)
