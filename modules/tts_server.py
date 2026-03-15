"""
TTS Server module - Flask server for TTS audio delivery and OBS dock control APIs.
"""

import json
import logging
import re
import time
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any

import pyttsx3
from flask import Flask, render_template_string, jsonify, send_file, request, Response, send_from_directory
from flask_cors import CORS

from config.config import get_config, AppConfig
from modules.llm_provider import LLMRequest, get_global_registry

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_ROOT = PROJECT_ROOT / "static"


PLAYER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Producer Audio Player</title>
    <style>
        body { margin: 0; padding: 20px; background: transparent; font-family: Arial, sans-serif; color: #fff; }
        #status { background: rgba(0, 0, 0, 0.7); padding: 10px; border-radius: 5px; margin-bottom: 10px; }
        .indicator { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; }
        .indicator.active { background: #0f0; box-shadow: 0 0 5px #0f0; }
        .indicator.idle { background: #666; }
        #debug { font-size: 12px; color: #888; margin-top: 10px; }
    </style>
</head>
<body>
    <div id="status">
        <span class="indicator idle" id="indicator"></span>
        <span id="statusText">Waiting for feedback...</span>
    </div>
    <div id="debug"></div>
    <audio id="audioPlayer" style="display:none;"></audio>
    <script>
        const audioPlayer = document.getElementById('audioPlayer');
        const indicator = document.getElementById('indicator');
        const statusText = document.getElementById('statusText');
        const debugDiv = document.getElementById('debug');
        let currentFilename = null;

        function updateDebug(msg) { debugDiv.innerHTML = `[${new Date().toLocaleTimeString()}] ${msg}`; }
        function setStatus(text, active = false) {
            statusText.textContent = text;
            indicator.className = active ? 'indicator active' : 'indicator idle';
        }

        async function checkForNewAudio() {
            try {
                const response = await fetch('/latest_tts', { cache: 'no-store' });
                const data = await response.json();
                if (data.filename && data.filename !== currentFilename) {
                    currentFilename = data.filename;
                    setStatus('Playing feedback...', true);
                    audioPlayer.src = `/audio/${data.filename}?t=${Date.now()}`;
                    audioPlayer.play().catch(err => {
                        setStatus('Playback error');
                        updateDebug(`Playback error: ${err.message}`);
                    });
                }
            } catch (error) {
                updateDebug(`Error checking for audio: ${error.message}`);
            }
        }

        audioPlayer.addEventListener('ended', () => {
            setStatus('Waiting for feedback...');
            updateDebug('Playback finished');
        });

        setInterval(checkForNewAudio, 1000);
        updateDebug('Player initialized, polling for audio...');
    </script>
</body>
</html>
"""


TELEPROMPTER_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Producer Teleprompter</title>
    <style>
        body { margin: 0; padding: 16px; background: rgba(0, 0, 0, 0.55); color: #fff; font-family: Arial, sans-serif; }
        #card { border: 1px solid rgba(255,255,255,0.25); border-radius: 10px; padding: 14px; min-height: 80px; background: rgba(20,20,20,0.75); }
        #meta { margin-top: 10px; font-size: 12px; color: #cfcfcf; }
        .priority { font-weight: bold; text-transform: uppercase; }
    </style>
</head>
<body>
    <div id="card">Waiting for guidance...</div>
    <div id="meta"></div>
    <script>
        const card = document.getElementById('card');
        const meta = document.getElementById('meta');
        let currentId = null;

        async function poll() {
            try {
                const resp = await fetch('/teleprompter/latest', { cache: 'no-store' });
                const data = await resp.json();
                if (!data.card) return;
                if (data.card.id !== currentId) {
                    currentId = data.card.id;
                    card.textContent = data.card.text;
                    meta.innerHTML = `<span class="priority">${data.card.priority}</span> • ${new Date(data.card.created_at * 1000).toLocaleTimeString()}`;
                }
            } catch (e) {
                meta.textContent = `Poll error: ${e.message}`;
            }
        }

        setInterval(poll, 1000);
        poll();
    </script>
</body>
</html>
"""


class TTSServer:
    """Flask server for TTS generation, teleprompter delivery, and OBS dock APIs."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()

        self.app = Flask(__name__)
        CORS(self.app)

        self.tts_engine: Optional[pyttsx3.Engine] = None
        self.tts_lock = threading.Lock()

        self.temp_dir = Path("temp")
        self.temp_dir.mkdir(exist_ok=True)
        self.latest_filename: Optional[str] = None
        self.filename_lock = threading.Lock()

        self.teleprompter_cards: deque = deque(maxlen=self.config.teleprompter_max_items)
        self.teleprompter_lock = threading.Lock()

        self.latest_guidance: Optional[Dict[str, Any]] = None
        self.latest_guidance_lock = threading.Lock()
        self.guidance_history: deque = deque(maxlen=50)
        self.pinned_guidance: deque = deque(maxlen=12)

        self.server_thread: Optional[threading.Thread] = None

        self._status_provider: Optional[Callable[[], Dict[str, Any]]] = None
        self._set_paused_callback: Optional[Callable[[bool], None]] = None
        self._set_lanes_callback: Optional[Callable[[bool, bool, bool], None]] = None
        self._manual_trigger_callback: Optional[Callable[[str, str, str], Dict[str, Any]]] = None
        self._pin_guidance_callback: Optional[Callable[[str], bool]] = None
        self._unpin_guidance_callback: Optional[Callable[[str], bool]] = None
        self._reconnect_obs_callback: Optional[Callable[[], Dict[str, Any]]] = None
        self._cancel_scene_guardrail_callback: Optional[Callable[[], bool]] = None
        self._hotkey_action_callback: Optional[Callable[[str], Dict[str, Any]]] = None
        self._get_session_status_callback: Optional[Callable[[], Dict[str, Any]]] = None
        self._session_kickoff_callback: Optional[Callable[[bool], Dict[str, Any]]] = None
        self._set_session_goals_callback: Optional[Callable[[str], Dict[str, Any]]] = None
        self._assign_scene_mode_callback: Optional[Callable[[str, str], Dict[str, Any]]] = None
        self._add_coaching_note_callback: Optional[Callable[[str, str, str], Dict[str, Any]]] = None
        self._toggle_training_mode_callback: Optional[Callable[[], Dict[str, Any]]] = None
        self._end_session_with_analysis_callback: Optional[Callable[[Optional[str]], Dict[str, Any]]] = None

        self._init_tts()
        self._setup_routes()

        logger.info(f"TTS Server initialized on {self.config.flask_host}:{self.config.flask_port}")

    def _init_tts(self) -> None:
        try:
            self.tts_engine = pyttsx3.init()
            self.tts_engine.setProperty('rate', self.config.tts_rate)
            self.tts_engine.setProperty('volume', self.config.tts_volume)
            logger.info("TTS engine initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize TTS engine: {e}")
            raise RuntimeError(f"TTS initialization failed: {e}")

    def _setup_routes(self) -> None:
        @self.app.route('/')
        def index():
            return '<h1>AI Producer TTS Server</h1><p>Open <a href="/obs_dock.html">/obs_dock.html</a> or <a href="/stream-director">/stream-director</a></p>'

        @self.app.route('/player.html')
        def player():
            return render_template_string(PLAYER_HTML)

        @self.app.route('/teleprompter.html')
        def teleprompter():
            return render_template_string(TELEPROMPTER_HTML)

        @self.app.route('/obs_dock.html')
        def obs_dock():
            static_path = STATIC_ROOT / "obs_dock.html"
            if static_path.exists():
                return send_file(static_path)
            return "OBS dock UI not found. Expected static/obs_dock.html", 404

        @self.app.route('/stream-director')
        @self.app.route('/stream-director/')
        def stream_director_index():
            static_path = STATIC_ROOT / "stream-director" / "index.html"
            if static_path.exists():
                return send_file(static_path)
            return "Stream Director UI not found. Expected static/stream-director/index.html", 404

        @self.app.route('/stream-director/<path:filename>')
        def stream_director_assets(filename):
            static_dir = STATIC_ROOT / "stream-director"
            file_path = static_dir / filename
            if not file_path.exists() or not file_path.is_file():
                return "Asset not found", 404
            return send_from_directory(static_dir.resolve(), filename)

        @self.app.route('/api/ai/pep-talk', methods=['POST'])
        def ai_pep_talk():
            payload = request.get_json(silent=True) or {}
            last_summary = str(payload.get('lastSummary', '')).strip()

            if not last_summary:
                return jsonify({
                    'ok': True,
                    'data': {'text': 'No previous summary found yet. Focus on clear speech, pacing, and confident delivery today.'},
                    'meta': {'fallback': True, 'reason': 'no-summary'},
                })

            prompt = (
                "Based on this last stream summary, provide a concise pep talk for the next stream. "
                "Return 4-6 short bullet points covering what to improve, what to avoid, and what to focus on.\n\n"
                f"Last stream summary:\n{last_summary}"
            )

            result = self._generate_ai_text(
                prompt=prompt,
                system_prompt=(
                    "You are an encouraging but direct talk-show producer coach for a streamer. "
                    "Be practical and actionable."
                ),
                max_tokens=220,
            )

            if not result['ok']:
                return jsonify({
                    'ok': True,
                    'data': {'text': 'Focus today: speak clearly, cut filler words, keep energy steady, and actively involve chat every few minutes.'},
                    'meta': {'fallback': True, 'reason': result['error']},
                })

            return jsonify({'ok': True, 'data': {'text': result['text']}, 'meta': result['meta']})

        @self.app.route('/api/ai/pre-stream-plan', methods=['POST'])
        def ai_pre_stream_plan():
            payload = request.get_json(silent=True) or {}
            plan_text = str(payload.get('planText', '')).strip()

            if not plan_text:
                return jsonify({'ok': True, 'data': {'text': 'Add a rough plan first: opener, core segment, chat interaction points, and a clear close.'}, 'meta': {'fallback': True, 'reason': 'no-plan'}})

            prompt = (
                "Turn this stream plan into practical producer notes. "
                "Provide 5 bullet points: opener hook, pacing checkpoints, chat prompts, risk to avoid, and closing beat.\n\n"
                f"User plan:\n{plan_text}"
            )

            result = self._generate_ai_text(
                prompt=prompt,
                system_prompt="You are a concise stream producer. Keep output short and stage-ready.",
                max_tokens=220,
            )

            if not result['ok']:
                fallback = (
                    "1) Start with a one-sentence hook.\n"
                    "2) Split content into 2-3 short segments.\n"
                    "3) Trigger chat interaction every 10 minutes.\n"
                    "4) Avoid long silent transitions.\n"
                    "5) End with a clear recap and next-stream teaser."
                )
                return jsonify({'ok': True, 'data': {'text': fallback}, 'meta': {'fallback': True, 'reason': result['error']}})

            return jsonify({'ok': True, 'data': {'text': result['text']}, 'meta': result['meta']})

        @self.app.route('/api/ai/during-analysis', methods=['POST'])
        def ai_during_analysis():
            payload = request.get_json(silent=True) or {}
            transcript = str(payload.get('transcript', '')).strip()
            metrics = payload.get('metrics', {}) if isinstance(payload.get('metrics', {}), dict) else {}

            if len(transcript) < 20:
                return jsonify({'ok': True, 'data': {'text': 'Need more speech before analysis. Keep narrating your actions clearly.'}, 'meta': {'fallback': True, 'reason': 'short-transcript'}})

            transcript_excerpt = transcript[-2500:]
            prompt = (
                "Analyze this recent stream transcript window and provide producer feedback in 1-3 short sentences. "
                "Focus on clarity, pacing, filler words, and engagement prompts.\n\n"
                f"Recent transcript:\n{transcript_excerpt}\n\n"
                f"Metrics:\n{json.dumps(metrics, ensure_ascii=False)}"
            )

            result = self._generate_ai_text(
                prompt=prompt,
                system_prompt="You are a live producer giving immediate, concise guidance.",
                max_tokens=140,
            )

            if not result['ok']:
                return jsonify({'ok': True, 'data': {'text': 'Producer note: reduce filler words, keep mic distance steady, and narrate your next action.'}, 'meta': {'fallback': True, 'reason': result['error']}})

            return jsonify({'ok': True, 'data': {'text': result['text']}, 'meta': result['meta']})

        @self.app.route('/api/ai/sensitive-topic-check', methods=['POST'])
        def ai_sensitive_topic_check():
            payload = request.get_json(silent=True) or {}
            message = str(payload.get('message', '')).strip()
            username = str(payload.get('username', 'viewer')).strip() or 'viewer'

            if not message:
                return jsonify({'ok': True, 'data': {'sensitive': False, 'suggestion': ''}, 'meta': {'fallback': True, 'reason': 'empty-message'}})

            keyword_hit = self._contains_sensitive_keywords(message)

            prompt = (
                "Classify whether this chat message is emotionally sensitive/trauma-heavy and needs a safe-topic redirect. "
                "Return strict JSON with keys: sensitive (boolean), suggestion (string under 30 words).\n\n"
                f"Username: {username}\n"
                f"Message: {message}"
            )

            result = self._generate_ai_text(
                prompt=prompt,
                system_prompt="You are a stream safety assistant. Prioritize boundaries and safe redirection.",
                max_tokens=120,
                temperature=0.2,
            )

            if result['ok']:
                parsed = self._parse_json_block(result['text'])
                if isinstance(parsed, dict) and 'sensitive' in parsed:
                    sensitive = bool(parsed.get('sensitive'))
                    suggestion = str(parsed.get('suggestion', '')).strip()
                    return jsonify({'ok': True, 'data': {'sensitive': sensitive, 'suggestion': suggestion}, 'meta': result['meta']})

            if keyword_hit:
                suggestion = (
                    f"{username} asked a sensitive question. Acknowledge briefly, set boundaries, and redirect to a lighter topic. "
                    "You are not a therapist."
                )
                return jsonify({'ok': True, 'data': {'sensitive': True, 'suggestion': suggestion}, 'meta': {'fallback': True, 'reason': 'keyword-heuristic'}})

            return jsonify({'ok': True, 'data': {'sensitive': False, 'suggestion': ''}, 'meta': {'fallback': True, 'reason': result.get('error', 'not-sensitive')}})

        @self.app.route('/api/ai/raid-welcome', methods=['POST'])
        def ai_raid_welcome():
            payload = request.get_json(silent=True) or {}
            stream_name = str(payload.get('streamName', 'My Stream')).strip() or 'My Stream'
            streamer_type = str(payload.get('streamerType', 'Variety')).strip() or 'Variety'
            current_game = str(payload.get('currentGame', 'Current Game')).strip() or 'Current Game'
            viewers = int(payload.get('viewers', 0) or 0)
            raider = str(payload.get('raider', 'unknown')).strip() or 'unknown'

            prompt = (
                "Generate exactly 4 concise bullet points for a raid welcome. "
                "Mention thanks, stream identity, what is happening now, and a chat invitation."
                " Return strict JSON with key bullets as an array of 4 strings.\n\n"
                f"stream_name={stream_name}\nstreamer_type={streamer_type}\ncurrent_game={current_game}\n"
                f"viewers={viewers}\nraider={raider}"
            )

            result = self._generate_ai_text(
                prompt=prompt,
                system_prompt="You are a live show producer. Keep bullets short, warm, and actionable.",
                max_tokens=180,
            )

            if result['ok']:
                parsed = self._parse_json_block(result['text'])
                if isinstance(parsed, dict) and isinstance(parsed.get('bullets'), list):
                    bullets = [str(item).strip() for item in parsed['bullets'] if str(item).strip()]
                    if bullets:
                        return jsonify({'ok': True, 'data': {'bullets': bullets[:5]}, 'meta': result['meta']})

            fallback_bullets = [
                f"Welcome raiders and thank {raider} for bringing {viewers} viewers.",
                f"Quick intro: this is {stream_name}, a {streamer_type} stream.",
                f"Recap what is happening now: we are currently on {current_game}.",
                "Invite new viewers to say hi and ask what content they enjoy.",
            ]
            return jsonify({'ok': True, 'data': {'bullets': fallback_bullets}, 'meta': {'fallback': True, 'reason': result.get('error', 'parse-failure')}})

        @self.app.route('/api/ai/post-summary', methods=['POST'])
        def ai_post_summary():
            payload = request.get_json(silent=True) or {}

            transcript = str(payload.get('transcript', '')).strip()
            speech_metrics = payload.get('speechMetrics', {}) if isinstance(payload.get('speechMetrics', {}), dict) else {}
            chat_events = payload.get('chatEvents', []) if isinstance(payload.get('chatEvents', []), list) else []
            setup_checks = payload.get('setupChecks', {}) if isinstance(payload.get('setupChecks', {}), dict) else {}

            transcript_excerpt = transcript[-3000:]
            chat_sample = chat_events[-20:]

            prompt = (
                "Create a post-stream coaching summary in 3 parts: WHAT WENT WELL, WHAT TO IMPROVE, NEXT STREAM FOCUS. "
                "Be direct, constructive, and concise (under 220 words).\n\n"
                f"Transcript excerpt:\n{transcript_excerpt}\n\n"
                f"Speech metrics:\n{json.dumps(speech_metrics, ensure_ascii=False)}\n\n"
                f"Chat events sample:\n{json.dumps(chat_sample, ensure_ascii=False)}\n\n"
                f"Setup checks:\n{json.dumps(setup_checks, ensure_ascii=False)}"
            )

            result = self._generate_ai_text(
                prompt=prompt,
                system_prompt="You are an experienced stream coach and producer.",
                max_tokens=300,
            )

            if not result['ok']:
                fallback = (
                    "WHAT WENT WELL: You completed the stream and kept moving through content.\n\n"
                    "WHAT TO IMPROVE: Reduce filler words, narrate decisions more often, and keep chat touchpoints consistent.\n\n"
                    "NEXT STREAM FOCUS: Practice one clarity habit (slow pace + clear enunciation) and one engagement habit (scheduled chat prompts)."
                )
                return jsonify({'ok': True, 'data': {'text': fallback}, 'meta': {'fallback': True, 'reason': result['error']}})

            return jsonify({'ok': True, 'data': {'text': result['text']}, 'meta': result['meta']})

        @self.app.route('/api/ai/status', methods=['GET'])
        def ai_status():
            registry = get_global_registry()
            providers = registry.list_providers()
            available_provider = registry.get_available_provider()

            provider_name = None
            provider_type = None
            if available_provider:
                provider_name = available_provider.name
                provider_type = available_provider.provider_type.value

            return jsonify({
                'ok': True,
                'data': {
                    'ai_live': available_provider is not None,
                    'fallback_mode': available_provider is None,
                    'active_provider_name': provider_name,
                    'active_provider_type': provider_type,
                    'providers': providers,
                    'fallback_chain': list(registry.fallback_chain),
                },
            })

        @self.app.route('/api/control', methods=['GET'])
        def control_status():
            if self._status_provider:
                return jsonify(self._status_provider())
            return jsonify(self._fallback_status())

        @self.app.route('/api/control/pause', methods=['POST'])
        def control_pause():
            payload = request.get_json(silent=True) or {}
            paused = bool(payload.get('paused', False))
            if self._set_paused_callback:
                self._set_paused_callback(paused)
            return jsonify(self._status_provider() if self._status_provider else {'guidance_paused': paused})

        @self.app.route('/api/control/lanes', methods=['POST'])
        def control_lanes():
            payload = request.get_json(silent=True) or {}
            in_ear_enabled = bool(payload.get('in_ear_enabled', True))
            teleprompter_enabled = bool(payload.get('teleprompter_enabled', True))
            scene_extensive_enabled = bool(payload.get('scene_extensive_enabled', True))
            if self._set_lanes_callback:
                self._set_lanes_callback(in_ear_enabled, teleprompter_enabled, scene_extensive_enabled)
            return jsonify(self._status_provider() if self._status_provider else {
                'lane_in_ear_enabled': in_ear_enabled,
                'lane_teleprompter_enabled': teleprompter_enabled,
                'scene_extensive_feedback_enabled': scene_extensive_enabled,
            })

        @self.app.route('/api/control/trigger', methods=['POST'])
        def control_trigger():
            payload = request.get_json(silent=True) or {}
            mode = str(payload.get('mode', 'normal'))
            intent = str(payload.get('intent', 'general'))
            focus_goal = str(payload.get('focus_goal', 'general'))
            result = {'published': False, 'mode': mode, 'intent': intent, 'focus_goal': focus_goal}
            if self._manual_trigger_callback:
                result = self._manual_trigger_callback(mode, intent, focus_goal)
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['manual_trigger_result'] = result
            return jsonify(status)

        @self.app.route('/api/control/pin', methods=['POST'])
        def control_pin():
            payload = request.get_json(silent=True) or {}
            guidance_id = str(payload.get('guidance_id', '')).strip()
            pinned = self._pin_guidance_callback(guidance_id) if (guidance_id and self._pin_guidance_callback) else False
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['pin_result'] = {'guidance_id': guidance_id, 'pinned': pinned}
            return jsonify(status)

        @self.app.route('/api/control/unpin', methods=['POST'])
        def control_unpin():
            payload = request.get_json(silent=True) or {}
            guidance_id = str(payload.get('guidance_id', '')).strip()
            unpinned = self._unpin_guidance_callback(guidance_id) if (guidance_id and self._unpin_guidance_callback) else False
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['unpin_result'] = {'guidance_id': guidance_id, 'unpinned': unpinned}
            return jsonify(status)

        @self.app.route('/api/control/reconnect_obs', methods=['POST'])
        def control_reconnect_obs():
            result = {'ok': False, 'error': 'Reconnect callback unavailable'}
            if self._reconnect_obs_callback:
                result = self._reconnect_obs_callback()
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['reconnect_result'] = result
            return jsonify(status)

        @self.app.route('/api/control/cancel_scene_guardrail', methods=['POST'])
        def control_cancel_scene_guardrail():
            cancelled = self._cancel_scene_guardrail_callback() if self._cancel_scene_guardrail_callback else False
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['cancel_scene_guardrail_result'] = {'cancelled': cancelled}
            return jsonify(status)

        @self.app.route('/api/hotkey', methods=['POST'])
        def hotkey_action():
            payload = request.get_json(silent=True) or {}
            action = str(payload.get('action', '')).strip()
            result = {'ok': False, 'action': action, 'error': 'Hotkey callback unavailable'}
            if self._hotkey_action_callback and action:
                result = self._hotkey_action_callback(action)
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['hotkey_result'] = result
            return jsonify(status)

        @self.app.route('/api/session/status', methods=['GET'])
        def session_status():
            result = {'ok': False, 'error': 'Session callback unavailable'}
            if self._get_session_status_callback:
                result = self._get_session_status_callback()
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['session_result'] = result
            return jsonify(status)

        @self.app.route('/api/session/kickoff', methods=['POST'])
        def session_kickoff():
            payload = request.get_json(silent=True) or {}
            repeat_goals = payload.get('repeat_goals', False)
            result = {'ok': False, 'error': 'Session kickoff callback unavailable'}
            if self._session_kickoff_callback:
                result = self._session_kickoff_callback(repeat_goals)
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['kickoff_result'] = result
            return jsonify(status)

        @self.app.route('/api/session/set_goals', methods=['POST'])
        def session_set_goals():
            payload = request.get_json(silent=True) or {}
            goals = str(payload.get('goals', '')).strip()
            result = {'ok': False, 'error': 'Session callback unavailable'}
            if self._set_session_goals_callback:
                result = self._set_session_goals_callback(goals)
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['set_goals_result'] = result
            return jsonify(status)

        @self.app.route('/api/session/assign_scene', methods=['POST'])
        def session_assign_scene():
            payload = request.get_json(silent=True) or {}
            scene_name = str(payload.get('scene_name', '')).strip()
            mode = str(payload.get('mode', '')).strip()
            result = {'ok': False, 'error': 'Session callback unavailable'}
            if self._assign_scene_mode_callback:
                result = self._assign_scene_mode_callback(scene_name, mode)
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['assign_scene_result'] = result
            return jsonify(status)

        @self.app.route('/api/session/add_note', methods=['POST'])
        def session_add_note():
            payload = request.get_json(silent=True) or {}
            category = str(payload.get('category', '')).strip()
            description = str(payload.get('description', '')).strip()
            severity = str(payload.get('severity', 'info')).strip()
            result = {'ok': False, 'error': 'Session callback unavailable'}
            if self._add_coaching_note_callback:
                result = self._add_coaching_note_callback(category, description, severity)
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['add_note_result'] = result
            return jsonify(status)

        @self.app.route('/api/training/toggle', methods=['POST'])
        def training_toggle():
            result = {'ok': False, 'error': 'Training callback unavailable'}
            if self._toggle_training_mode_callback:
                result = self._toggle_training_mode_callback()
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['training_result'] = result
            return jsonify(status)

        @self.app.route('/api/training/end_session', methods=['POST'])
        def training_end_session():
            payload = request.get_json(silent=True) or {}
            user_notes = str(payload.get('notes', '')).strip() or None
            result = {'ok': False, 'error': 'Training callback unavailable'}
            if self._end_session_with_analysis_callback:
                result = self._end_session_with_analysis_callback(user_notes)
            status = self._status_provider() if self._status_provider else self._fallback_status()
            status['end_session_result'] = result
            return jsonify(status)

        @self.app.route('/api/training/status', methods=['GET'])
        def training_status():
            # Return current training mode status from the status provider
            status = self._status_provider() if self._status_provider else self._fallback_status()
            return jsonify({'training_mode': status.get('training_mode_active', False)})

        @self.app.route('/api/export/session', methods=['GET'])
        def export_session():
            fmt = request.args.get('format', 'json').lower().strip()
            session = {
                'exported_at': time.time(),
                'latest_guidance': self.get_latest_guidance(),
                'recent_guidance': self.get_recent_guidance(limit=50),
                'pinned_guidance': self.get_pinned_guidance(),
            }
            if fmt == 'md':
                return Response(self._to_markdown_export(session), mimetype='text/markdown')
            return jsonify(session)

        @self.app.route('/latest_tts')
        def latest_tts():
            with self.filename_lock:
                return jsonify({'filename': self.latest_filename, 'timestamp': time.time()})

        @self.app.route('/audio/<filename>')
        def serve_audio(filename):
            file_path = self.temp_dir / filename
            if not file_path.exists():
                logger.warning(f"Audio file not found: {filename}")
                return "File not found", 404
            return send_file(file_path, mimetype='audio/mpeg', as_attachment=False)

        @self.app.route('/teleprompter/latest')
        def teleprompter_latest():
            return jsonify({'card': self.get_latest_teleprompter_card()})

        @self.app.route('/teleprompter/queue')
        def teleprompter_queue():
            with self.teleprompter_lock:
                self._cleanup_expired_teleprompter_cards()
                return jsonify({'cards': list(self.teleprompter_cards)})

        @self.app.route('/health')
        def health():
            return jsonify({
                'status': 'ok',
                'latest_audio': self.latest_filename,
                'temp_dir': str(self.temp_dir),
                'audio_files': len(list(self.temp_dir.glob('*.mp3'))),
            })

    def _generate_ai_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        max_tokens: int = 200,
        temperature: float = 0.4,
    ) -> Dict[str, Any]:
        registry = get_global_registry()
        provider = registry.get_available_provider()

        if not provider:
            return {'ok': False, 'error': 'no-available-provider', 'text': '', 'meta': {}}

        request_payload = LLMRequest(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=0.9,
            timeout_sec=20.0,
        )

        response = provider.generate(request_payload)

        if response.error:
            logger.warning(f"AI endpoint generation failed ({provider.name}): {response.error}")
            return {
                'ok': False,
                'error': response.error,
                'text': '',
                'meta': {'provider': provider.name, 'model': response.model},
            }

        return {
            'ok': True,
            'error': None,
            'text': (response.text or '').strip(),
            'meta': {
                'provider': response.provider,
                'model': response.model,
                'latency_sec': response.latency_sec,
                'finish_reason': response.finish_reason,
                'tokens_used': response.tokens_used or {},
            },
        }

    @staticmethod
    def _parse_json_block(text: str) -> Optional[Any]:
        if not text:
            return None

        cleaned = text.strip()

        if cleaned.startswith('```'):
            cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r'\s*```$', '', cleaned)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        object_match = re.search(r'\{[\s\S]*\}', cleaned)
        if object_match:
            try:
                return json.loads(object_match.group(0))
            except json.JSONDecodeError:
                return None

        array_match = re.search(r'\[[\s\S]*\]', cleaned)
        if array_match:
            try:
                return json.loads(array_match.group(0))
            except json.JSONDecodeError:
                return None

        return None

    @staticmethod
    def _contains_sensitive_keywords(message: str) -> bool:
        lowered = (message or '').lower()
        sensitive_terms = [
            'suicide',
            'self-harm',
            'abuse',
            'assault',
            'depressed',
            'depression',
            'trauma',
            'panic attack',
            'ptsd',
            'kill myself',
            'hurt myself',
            'grief',
            'overdose',
        ]
        return any(term in lowered for term in sensitive_terms)

    def _fallback_status(self) -> Dict[str, Any]:
        return {
            'guidance_paused': False,
            'lane_in_ear_enabled': True,
            'lane_teleprompter_enabled': True,
            'scene_extensive_feedback_enabled': True,
            'scene_auto_coaching_active': False,
            'scene_mode': 'normal',
            'scene_name': '',
            'scene_match_pattern': '',
            'scene_connection_status': 'disabled',
            'scene_connection_error': '',
            'safety_level': 'unknown',
            'safety_banner': 'Safety status unavailable',
            'event_drop_rate': 0.0,
            'chat_total_messages': 0,
            'voice_total_chunks': 0,
            'cooldowns': {'in_ear_remaining': 0.0, 'teleprompter_remaining': 0.0},
            'last_guidance': self.get_latest_guidance(),
            'recent_guidance': self.get_recent_guidance(limit=5),
            'pinned_guidance': self.get_pinned_guidance(),
            'pending_scene_guardrail': {'id': '', 'mode': '', 'due_at': 0.0, 'active': False},
            'training_mode_active': False,
        }

    def _to_markdown_export(self, session: Dict[str, Any]) -> str:
        lines = [
            "# Stream Session Recap",
            "",
            f"Exported: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(session['exported_at']))}",
            "",
            "## Pinned Guidance",
        ]
        pinned = session.get('pinned_guidance') or []
        if not pinned:
            lines.append("- None")
        else:
            for item in pinned:
                lines.append(f"- {item.get('text', '')}")

        lines.append("")
        lines.append("## Recent Guidance")
        recent = session.get('recent_guidance') or []
        if not recent:
            lines.append("- None")
        else:
            for item in recent:
                meta = []
                if item.get('priority'):
                    meta.append(item['priority'])
                if item.get('provider'):
                    meta.append(item['provider'])
                meta_text = f" ({', '.join(meta)})" if meta else ""
                lines.append(f"- {item.get('text', '')}{meta_text}")

        return "\n".join(lines)

    def generate_audio(self, text: str) -> Optional[str]:
        if not text:
            logger.warning("Cannot generate audio from empty text")
            return None

        try:
            timestamp = int(time.time() * 1000)
            filename = f"feedback_{timestamp}.mp3"
            filepath = self.temp_dir / filename

            if not self.tts_engine:
                logger.error("TTS engine is not initialized")
                return None

            with self.tts_lock:
                self.tts_engine.save_to_file(text, str(filepath))
                self.tts_engine.runAndWait()

            if not filepath.exists():
                logger.error(f"TTS file not created: {filepath}")
                return None

            with self.filename_lock:
                self.latest_filename = filename

            self._cleanup_old_files()
            return filename

        except Exception as e:
            logger.error(f"Failed to generate TTS audio: {e}", exc_info=True)
            return None

    def _cleanup_old_files(self, max_age_seconds: float = 60.0) -> None:
        try:
            cutoff_time = time.time() - max_age_seconds
            for filepath in self.temp_dir.glob('feedback_*.mp3'):
                if filepath.stat().st_mtime < cutoff_time:
                    filepath.unlink()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

    def _cleanup_expired_teleprompter_cards(self) -> None:
        now = time.time()
        while self.teleprompter_cards and self.teleprompter_cards[0]['expires_at'] <= now:
            self.teleprompter_cards.popleft()

    def publish_teleprompter(self, text: str, priority: str = "normal") -> Optional[str]:
        if not text:
            return None

        card_id = str(uuid.uuid4())
        now = time.time()
        card = {
            'id': card_id,
            'text': text,
            'priority': priority,
            'created_at': now,
            'expires_at': now + self.config.teleprompter_ttl_seconds,
        }

        with self.teleprompter_lock:
            self._cleanup_expired_teleprompter_cards()
            self.teleprompter_cards.append(card)

        return card_id

    def get_latest_teleprompter_card(self) -> Optional[Dict]:
        with self.teleprompter_lock:
            self._cleanup_expired_teleprompter_cards()
            if not self.teleprompter_cards:
                return None
            return self.teleprompter_cards[-1]

    def record_latest_guidance(
        self,
        text: str,
        priority: str,
        provider: str,
        reason: str,
        send_in_ear: bool,
        send_teleprompter: bool,
    ) -> None:
        if not text:
            return

        payload = {
            "id": str(uuid.uuid4()),
            "text": text,
            "priority": priority,
            "provider": provider,
            "reason": reason,
            "send_in_ear": send_in_ear,
            "send_teleprompter": send_teleprompter,
            "created_at": time.time(),
        }

        with self.latest_guidance_lock:
            self.latest_guidance = payload
            self.guidance_history.append(payload)

    def get_latest_guidance(self) -> Optional[Dict[str, Any]]:
        with self.latest_guidance_lock:
            if not self.latest_guidance:
                return None
            return dict(self.latest_guidance)

    def get_recent_guidance(self, limit: int = 5) -> List[Dict[str, Any]]:
        with self.latest_guidance_lock:
            items = list(self.guidance_history)[-limit:]
            return [dict(item) for item in reversed(items)]

    def get_pinned_guidance(self) -> List[Dict[str, Any]]:
        with self.latest_guidance_lock:
            return [dict(item) for item in reversed(list(self.pinned_guidance))]

    def pin_guidance(self, guidance_id: str) -> bool:
        if not guidance_id:
            return False

        with self.latest_guidance_lock:
            if any(item.get("id") == guidance_id for item in self.pinned_guidance):
                return True
            source = None
            for item in reversed(self.guidance_history):
                if item.get("id") == guidance_id:
                    source = dict(item)
                    break
            if source is None:
                return False
            self.pinned_guidance.append(source)
            return True

    def unpin_guidance(self, guidance_id: str) -> bool:
        if not guidance_id:
            return False

        with self.latest_guidance_lock:
            remaining = [item for item in self.pinned_guidance if item.get("id") != guidance_id]
            if len(remaining) == len(self.pinned_guidance):
                return False
            self.pinned_guidance = deque(remaining, maxlen=self.pinned_guidance.maxlen)
            return True

    def run(self, debug: bool = False) -> None:
        logger.info(f"Starting Flask server on http://{self.config.flask_host}:{self.config.flask_port}")
        if not debug:
            import logging as flask_logging
            flask_log = flask_logging.getLogger('werkzeug')
            flask_log.setLevel(flask_logging.WARNING)

        self.app.run(
            host=self.config.flask_host,
            port=self.config.flask_port,
            debug=debug,
            threaded=True,
            use_reloader=False,
        )

    def run_in_thread(self) -> threading.Thread:
        thread = threading.Thread(target=self.run, kwargs={'debug': False}, daemon=True)
        thread.start()
        self.server_thread = thread
        logger.info("Flask server started in background thread")
        return thread

    def set_dock_callbacks(
        self,
        status_provider: Callable[[], Dict[str, Any]],
        set_paused_callback: Callable[[bool], None],
        set_lanes_callback: Optional[Callable[[bool, bool, bool], None]] = None,
        manual_trigger_callback: Optional[Callable[[str, str, str], Dict[str, Any]]] = None,
        pin_guidance_callback: Optional[Callable[[str], bool]] = None,
        unpin_guidance_callback: Optional[Callable[[str], bool]] = None,
        reconnect_obs_callback: Optional[Callable[[], Dict[str, Any]]] = None,
        cancel_scene_guardrail_callback: Optional[Callable[[], bool]] = None,
        hotkey_action_callback: Optional[Callable[[str], Dict[str, Any]]] = None,
        get_session_status_callback: Optional[Callable[[], Dict[str, Any]]] = None,
        session_kickoff_callback: Optional[Callable[[bool], Dict[str, Any]]] = None,
        set_session_goals_callback: Optional[Callable[[str], Dict[str, Any]]] = None,
        assign_scene_mode_callback: Optional[Callable[[str, str], Dict[str, Any]]] = None,
        add_coaching_note_callback: Optional[Callable[[str, str, str], Dict[str, Any]]] = None,
        toggle_training_mode_callback: Optional[Callable[[], Dict[str, Any]]] = None,
        end_session_with_analysis_callback: Optional[Callable[[Optional[str]], Dict[str, Any]]] = None,
    ) -> None:
        self._status_provider = status_provider
        self._set_paused_callback = set_paused_callback
        self._set_lanes_callback = set_lanes_callback
        self._manual_trigger_callback = manual_trigger_callback
        self._pin_guidance_callback = pin_guidance_callback
        self._unpin_guidance_callback = unpin_guidance_callback
        self._reconnect_obs_callback = reconnect_obs_callback
        self._cancel_scene_guardrail_callback = cancel_scene_guardrail_callback
        self._hotkey_action_callback = hotkey_action_callback
        self._get_session_status_callback = get_session_status_callback
        self._session_kickoff_callback = session_kickoff_callback
        self._set_session_goals_callback = set_session_goals_callback
        self._assign_scene_mode_callback = assign_scene_mode_callback
        self._add_coaching_note_callback = add_coaching_note_callback
        self._toggle_training_mode_callback = toggle_training_mode_callback
        self._end_session_with_analysis_callback = end_session_with_analysis_callback

    def start(self) -> None:
        if self.server_thread and self.server_thread.is_alive():
            logger.warning("TTS server already running")
            return
        self.run_in_thread()

    def stop(self) -> None:
        logger.info("TTSServer stop requested (daemon thread exits on process shutdown)")


_tts_server: Optional[TTSServer] = None


def get_tts_server() -> TTSServer:
    global _tts_server
    if _tts_server is None:
        _tts_server = TTSServer()
    return _tts_server


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    server = TTSServer()
    server.run(debug=True)
