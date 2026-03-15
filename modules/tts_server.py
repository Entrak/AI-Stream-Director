"""
TTS Server module - Flask server for TTS audio delivery and OBS dock control APIs.
"""

import json
import logging
import time
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Optional, Dict, List, Callable, Any

import pyttsx3
from flask import Flask, render_template_string, jsonify, send_file, request, Response
from flask_cors import CORS

from config.config import get_config, AppConfig

logger = logging.getLogger(__name__)


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
            return '<h1>AI Producer TTS Server</h1><p>Open <a href="/obs_dock.html">/obs_dock.html</a></p>'

        @self.app.route('/player.html')
        def player():
            return render_template_string(PLAYER_HTML)

        @self.app.route('/teleprompter.html')
        def teleprompter():
            return render_template_string(TELEPROMPTER_HTML)

        @self.app.route('/obs_dock.html')
        def obs_dock():
            static_path = Path("static") / "obs_dock.html"
            if static_path.exists():
                return send_file(static_path)
            return "OBS dock UI not found. Expected static/obs_dock.html", 404

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
