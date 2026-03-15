"""
Twitch AI Stream Producer - Main Orchestration

Coordinates all components:
- Chat reader (Twitch-native, OCR fallback)
- Voice analyzer (Whisper STT)
- AI producer (Ollama feedback)
- TTS server (Flask audio delivery)
"""

import argparse
import logging
import os
import signal
import sys
import time
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Any

from config.config import get_config_manager, get_config
from modules.setup_wizard import run_calibration_wizard
from modules.chat_reader import ChatReader
from modules.twitch_chat_reader import TwitchChatReader
from modules.voice_analyzer import VoiceAnalyzer
from modules.ai_producer import AIProducer
from modules.tts_server import TTSServer
from modules.twitch_stream_stats import TwitchStreamStats
from modules.stream_safety_manager import StreamSafetyManager
from modules.adaptive_inference_router import AdaptiveInferenceRouter
from modules.llm_provider import (
    ProviderRegistry,
    OllamaProvider,
    ProviderType,
    get_global_registry,
)
from core.events import ChatSnapshot, VoiceSnapshot
from core.guidance_router import GuidanceRouter

# Setup logging
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_DIR / 'app.log'),
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


class StreamProducer:
    """
    Main application orchestrator
    
    Coordinates all subsystems and manages feedback generation loop.
    """
    
    def __init__(self):
        self.config_manager = get_config_manager()
        self.config = self.config_manager.get_config()
        
        # Components
        self.chat_reader: Optional[Any] = None
        self.voice_analyzer: Optional[VoiceAnalyzer] = None
        self.ai_producer: Optional[AIProducer] = None
        self.tts_server: Optional[TTSServer] = None
        self.guidance_router: Optional[GuidanceRouter] = None
        self.twitch_stats: Optional[TwitchStreamStats] = None
        
        # Safety and adaptive routing (Phase 1 features)
        self.safety_manager: Optional[StreamSafetyManager] = None
        self.inference_router: Optional[AdaptiveInferenceRouter] = None
        self.provider_registry: Optional[ProviderRegistry] = None
        
        # Control flags
        self.running = False
        self.ai_thread: Optional[threading.Thread] = None
        
        logger.info("Stream Producer initialized")
    
    def _init_components(self) -> None:
        """Initialize all components"""
        logger.info("Initializing components...")
        
        try:
            # Initialize chat reader (Twitch native preferred, OCR fallback)
            mode = (self.config.chat_ingestion_mode or "ocr").lower().strip()
            if mode == "twitch":
                try:
                    self.chat_reader = TwitchChatReader(self.config)
                    logger.info("✓ Twitch chat reader initialized")
                except Exception as e:
                    if not self.config.is_calibrated():
                        raise RuntimeError(
                            f"Twitch chat reader failed ({e}) and OCR fallback is unavailable because chat region is not calibrated"
                        )
                    logger.warning(f"Twitch chat reader failed: {e}. Falling back to OCR reader.")
                    self.chat_reader = ChatReader(self.config)
                    logger.info("✓ OCR chat reader initialized (fallback)")
            else:
                self.chat_reader = ChatReader(self.config)
                logger.info("✓ OCR chat reader initialized")
            
            # Initialize voice analyzer
            self.voice_analyzer = VoiceAnalyzer(self.config)
            logger.info("✓ Voice analyzer initialized")
            
            # Initialize AI producer
            self.ai_producer = AIProducer(self.config)
            logger.info("✓ AI producer initialized")
            
            # Initialize TTS server
            self.tts_server = TTSServer(self.config)
            logger.info("✓ TTS server initialized")

            # Initialize Twitch stream stats (optional)
            self.twitch_stats = TwitchStreamStats(self.config)
            logger.info("✓ Twitch stream stats initialized")

            # Initialize guidance router
            self.guidance_router = GuidanceRouter(self.config)
            logger.info("✓ Guidance router initialized")
            
            # Initialize Stream Safety Manager (Phase 1 feature)
            self.safety_manager = StreamSafetyManager()
            logger.info("✓ Stream Safety Manager initialized")
            
            # Initialize Provider Registry and register Ollama
            self.provider_registry = get_global_registry()
            
            # Register Ollama provider
            ollama_provider = OllamaProvider(
                host=self.config.ollama_host,
                model=self.config.ollama_model
            )
            self.provider_registry.register("ollama", ollama_provider)
            
            # TODO: In future, register cloud providers (OpenAI, Anthropic)
            # when credentials are available in config
            
            # Set fallback chain (for now, just Ollama; extend as providers added)
            self.provider_registry.set_fallback_chain(["ollama"])
            logger.info("✓ Provider Registry initialized")
            
            # Initialize Adaptive Inference Router (Phase 1 feature)
            self.inference_router = AdaptiveInferenceRouter(
                safety_manager=self.safety_manager,
                provider_registry=self.provider_registry,
            )
            logger.info("✓ Adaptive Inference Router initialized")
            
        except Exception as e:
            logger.error(f"Failed to initialize components: {e}", exc_info=True)
            raise
    
    def _ai_processing_loop(self) -> None:
        """
        AI processing loop (runs in separate thread)
        
        Periodically checks triggers and generates feedback with stream-safety guarantees.
        Uses AdaptiveInferenceRouter to respect resource constraints.
        """
        logger.info("AI processing loop started")
        
        while self.running:
            try:
                if not self.chat_reader or not self.voice_analyzer or not self.ai_producer or not self.tts_server or not self.guidance_router or not self.inference_router:
                    logger.error("Components not fully initialized in AI loop")
                    time.sleep(1.0)
                    continue

                # Get current data from components
                recent_messages = self.chat_reader.get_recent_messages(10)
                new_users = self.chat_reader.get_new_users()
                
                chat_data = {
                    'recent_message_count': self.chat_reader.get_message_count(30.0),
                    'total_messages': len(self.chat_reader.messages)
                }
                
                voice_data = self.voice_analyzer.get_average_metrics(60.0)
                
                # Check if feedback should be generated
                if self.ai_producer.should_trigger(chat_data, voice_data, new_users):
                    logger.info("Checking if inference is safe...")
                    
                    # Generate feedback prompt
                    feedback_prompt = self.ai_producer._build_prompt(
                        chat_data,
                        voice_data,
                        new_users,
                        recent_messages
                    )
                    
                    # Route through adaptive inference router for safety check
                    # The router will skip if stream resources are too constrained
                    response = self.inference_router.generate_guidance(
                        prompt=feedback_prompt,
                        system_prompt=(
                            "You are an AI stream producer for a Twitch streamer. "
                            "Give 1-2 actionable, encouraging tips in under 50 words. "
                            "Be specific and concise. Focus on the most important issue."
                        ),
                        context_data={"chat": chat_data, "voice": voice_data}
                    )
                    
                    if response and response.error is None:
                        # Inference succeeded and is safe
                        feedback = response.text
                        
                        chat_snapshot = ChatSnapshot(
                            recent_message_count=chat_data.get('recent_message_count', 0),
                            total_messages=chat_data.get('total_messages', 0),
                            new_users=new_users,
                        )
                        voice_snapshot = VoiceSnapshot(
                            words_per_minute=voice_data.get('words_per_minute', 0.0),
                            filler_count=voice_data.get('filler_count', 0),
                            energy_level=voice_data.get('energy_level', 0.0),
                        )

                        decision = self.guidance_router.route(feedback, chat_snapshot, voice_snapshot)
                        if not decision:
                            logger.debug("Guidance router skipped delivery due to lane cooldown policy")
                            time.sleep(self.config.ai_processing_interval)
                            continue

                        delivered = False

                        if decision.send_teleprompter:
                            card_id = self.tts_server.publish_teleprompter(decision.text, decision.priority)
                            if card_id:
                                delivered = True

                        if decision.send_in_ear:
                            audio_file = self.tts_server.generate_audio(decision.text)
                            if audio_file:
                                delivered = True
                            else:
                                logger.error("Failed to generate TTS audio")

                        if delivered:
                            logger.info(f"✓ Guidance delivered ({decision.priority}): {decision.reason}")
                    elif response is None:
                        # Router skipped inference to protect stream
                        logger.info("Inference skipped to protect stream resources")
                    else:
                        logger.warning(f"Inference failed: {response.error}")
                
                # Wait before next check
                time.sleep(self.config.ai_processing_interval)
            
            except Exception as e:
                logger.error(f"Error in AI processing loop: {e}", exc_info=True)
                time.sleep(5.0)
    
    def start(self) -> None:
        """Start all components"""
        if self.running:
            logger.warning("Stream producer already running")
            return

        if not self.config.setup_completed:
            logger.info("First-run setup is not complete. Run .\\run.ps1 -Preflight for guided checks and Twitch authorization.")
        
        # Validate configuration
        if not self.config_manager.validate():
            logger.error("Configuration validation failed")
            raise RuntimeError("Invalid configuration")
        
        mode = (self.config.chat_ingestion_mode or "ocr").lower().strip()
        if mode == "ocr" and not self.config.is_calibrated():
            logger.error("Chat region not calibrated. Run with --calibrate flag.")
            raise RuntimeError("Chat region not calibrated")
        
        # Initialize components
        self._init_components()

        if not self.chat_reader or not self.voice_analyzer or not self.ai_producer or not self.tts_server or not self.guidance_router or not self.inference_router:
            raise RuntimeError("Component initialization incomplete")
        
        logger.info("="*60)
        logger.info("STARTING STREAM PRODUCER")
        logger.info("="*60)
        
        self.running = True
        
        # Start adaptive inference router
        if self.inference_router:
            self.inference_router.start()
        
        # Start TTS server (in background thread)
        self.tts_server.run_in_thread()
        time.sleep(1.0)  # Give server time to start
        
        # Start chat reader
        self.chat_reader.start()
        
        # Start voice analyzer
        self.voice_analyzer.start()

        # Start Twitch stream stats polling (optional if credentials are set)
        if self.twitch_stats:
            self.twitch_stats.start()
        
        # Start AI processing loop
        self.ai_thread = threading.Thread(
            target=self._ai_processing_loop,
            daemon=True
        )
        self.ai_thread.start()
        
        logger.info("="*60)
        logger.info("ALL SYSTEMS RUNNING")
        logger.info("="*60)
        logger.info(f"TTS Player: http://{self.config.flask_host}:{self.config.flask_port}/player.html")
        logger.info(f"Teleprompter: http://{self.config.flask_host}:{self.config.flask_port}/teleprompter.html")
        logger.info(f"Health Check: http://{self.config.flask_host}:{self.config.flask_port}/health")
        logger.info("="*60)
    
    def stop(self) -> None:
        """Stop all components"""
        if not self.running:
            return
        
        logger.info("="*60)
        logger.info("STOPPING STREAM PRODUCER")
        logger.info("="*60)
        
        self.running = False
        
        # Stop safety manager and inference router
        if self.inference_router:
            self.inference_router.stop()
        
        if self.safety_manager:
            self.safety_manager.stop_monitoring()
        
        # Stop components
        if self.chat_reader:
            self.chat_reader.stop()
        
        if self.voice_analyzer:
            self.voice_analyzer.stop()

        if self.twitch_stats:
            self.twitch_stats.stop()
        
        # Wait for AI thread
        if self.ai_thread:
            self.ai_thread.join(timeout=5.0)
        
        logger.info("All components stopped")
    
    def print_status(self) -> None:
        """Print current status of all components"""
        if not self.running:
            print("Stream producer is not running")
            return

        if not self.chat_reader or not self.voice_analyzer or not self.ai_producer:
            print("Components are not fully initialized")
            return
        
        print("\n" + "="*60)
        print("STREAM PRODUCER STATUS")
        print("="*60)
        
        # Chat reader stats
        chat_stats = self.chat_reader.get_stats()
        print(f"\n📝 Chat Reader:")
        print(f"  Captures: {chat_stats.get('total_captures', 0)}")
        print(f"  Success rate: {chat_stats.get('success_rate', 0.0):.1f}%")
        print(f"  Total messages: {chat_stats['total_messages']}")
        print(f"  Unique users: {chat_stats['unique_users']}")
        
        # Voice analyzer stats
        voice_stats = self.voice_analyzer.get_stats()
        voice_metrics = self.voice_analyzer.get_average_metrics(60.0)
        print(f"\n🎤 Voice Analyzer:")
        print(f"  Chunks processed: {voice_stats['total_chunks']}")
        print(f"  Avg transcription time: {voice_stats['avg_transcription_time']:.2f}s")
        print(f"  Words/min: {voice_metrics['words_per_minute']:.0f}")
        print(f"  Filler count: {voice_metrics['filler_count']}")
        
        # AI producer stats
        ai_stats = self.ai_producer.get_stats()
        print(f"\n🤖 AI Producer:")
        print(f"  Total feedbacks: {ai_stats['total_feedbacks']}")
        print(f"  Time since last: {ai_stats['time_since_last']:.0f}s")
        
        # Stream Safety Manager stats (Phase 1 feature)
        if self.safety_manager:
            safety_stats = self.safety_manager.get_stats()
            print(f"\n🛡️  Stream Safety:")
            print(f"  Safety level: {safety_stats['safety_level']}")
            print(f"  CPU available: {safety_stats['headroom']['cpu_available']:.1f}%")
            print(f"  Memory available: {safety_stats['headroom']['memory_available']:.1f}%")
            if safety_stats['headroom']['gpu_available'] is not None:
                print(f"  GPU available: {safety_stats['headroom']['gpu_available']:.1f}%")
            print(f"  Safety checks total: {safety_stats['checks_total']}")
            print(f"  Unsafe triggers: {safety_stats['unsafe_triggers']}")
        
        # Adaptive Inference Router stats (Phase 1 feature)
        if self.inference_router:
            routing_stats = self.inference_router.get_stats()
            print(f"\n🔀 Inference Routing:")
            print(f"  Total requests: {routing_stats['total_requests']}")
            print(f"  Successful: {routing_stats['successful']}")
            print(f"  Skipped: {routing_stats['skipped']}")
            print(f"  Fallbacks: {routing_stats['fallbacks_used']}")
            print(f"  Success rate: {routing_stats['success_rate']:.1f}%")

        # Twitch stream stats (if configured)
        if self.twitch_stats:
            stream_stats = self.twitch_stats.get_latest_stats()
            print(f"\n📈 Twitch Stream:")
            if not self.twitch_stats.is_configured():
                print("  API Status: Not configured (set TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET and twitch_channel)")
            elif self.config.twitch_require_user_auth and not self.twitch_stats.oauth.has_valid_token():
                print("  API Status: Authorization required (browser login prompt expected)")
            else:
                print(f"  Live: {'Yes' if stream_stats.get('is_live') else 'No'}")
                print(f"  Viewers: {stream_stats.get('viewer_count', 0)}")
                print(f"  Game: {stream_stats.get('game_name', '-') or '-'}")
                if stream_stats.get('title'):
                    print(f"  Title: {stream_stats.get('title')}")
                if stream_stats.get('error'):
                    print(f"  API Status: {stream_stats.get('error')}")
        
        print("="*60 + "\n")

    def run_preflight(self) -> bool:
        """Run first-time setup checks without starting long-running loops."""
        print("\n" + "="*60)
        print("PREFLIGHT CHECK")
        print("="*60)

        checks = []

        config_ok = self.config_manager.validate()
        checks.append(
            {
                "name": "Config validation",
                "ok": config_ok,
                "hint": "Set a real twitch_channel in config/user_config.json if placeholder is used",
            }
        )

        try:
            req = urllib.request.Request(f"{self.config.ollama_host}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=8.0):
                pass
            checks.append({"name": "Ollama reachable", "ok": True, "hint": ""})
        except Exception as e:
            checks.append(
                {
                    "name": "Ollama reachable",
                    "ok": False,
                    "hint": f"Run 'ollama serve' and ensure endpoint {self.config.ollama_host} is available ({e})",
                }
            )

        client_id_present = bool((os.getenv("TWITCH_CLIENT_ID") or "").strip())
        client_secret_present = bool((os.getenv("TWITCH_CLIENT_SECRET") or "").strip())
        env_ok = client_id_present and client_secret_present
        checks.append(
            {
                "name": "Twitch credentials present",
                "ok": env_ok,
                "hint": "Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in .env or environment",
            }
        )

        twitch_stats = TwitchStreamStats(self.config)
        if env_ok and twitch_stats.is_configured():
            probe = twitch_stats.probe_once(interactive_auth=True)
            if probe.get("ok"):
                stats = probe.get("stats", {})
                checks.append(
                    {
                        "name": "Twitch API authorization",
                        "ok": True,
                        "hint": f"Authorized (live={'yes' if stats.get('is_live') else 'no'}, viewers={stats.get('viewer_count', 0)})",
                    }
                )
            else:
                checks.append(
                    {
                        "name": "Twitch API authorization",
                        "ok": False,
                        "hint": probe.get("error", "unknown Twitch API error"),
                    }
                )
        else:
            checks.append(
                {
                    "name": "Twitch API authorization",
                    "ok": False,
                    "hint": "Set valid twitch_channel and credentials, then rerun preflight",
                }
            )

        # Stream Safety Assessment (Phase 1 feature)
        safety_mgr = StreamSafetyManager()
        headroom = safety_mgr.get_headroom()
        safety_level = safety_mgr.assess_safety()
        
        safety_ok = safety_level.value != "unsafe"
        checks.append(
            {
                "name": "Stream Safety Assessment",
                "ok": safety_ok,
                "hint": f"Safety level: {safety_level.value} | CPU: {headroom.cpu_available:.0f}% avail | RAM: {headroom.memory_available:.0f}% avail",
            }
        )
        
        # Recommendation for safe mode
        if safety_level.value == "degraded":
            print("\n⚠️  RESOURCE WARNING: Your system has moderate constraints.")
            print("  The assistant will use reduced context windows and response lengths.")
            print("  This ensures stream quality is never impacted.")
        elif safety_level.value == "minimal":
            print("\n⚠️  RESOURCE WARNING: Your system has low headroom.")
            print("  The assistant will only run critical features with minimal inference.")
            print("  Consider closing other applications or optimizing your setup.")

        all_ok = all(item["ok"] for item in checks)

        for item in checks:
            prefix = "✓" if item["ok"] else "✗"
            print(f"{prefix} {item['name']}")
            if item["hint"]:
                print(f"  → {item['hint']}")

        if all_ok:
            if not self.config.setup_completed:
                self.config.setup_completed = True
                self.config_manager.save()
            print("\nSetup marked as completed.")
        else:
            print("\nNext actions:")
            for item in checks:
                if not item["ok"]:
                    print(f"- {item['name']}: {item['hint']}")

        print("="*60 + "\n")
        return all_ok


def main():
    """Main entry point"""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description="Twitch AI Stream Producer - Local AI feedback for streamers"
    )
    parser.add_argument(
        '--calibrate',
        action='store_true',
        help='Run calibration wizard to set chat region'
    )
    parser.add_argument(
        '--status-interval',
        type=int,
        default=30,
        help='Print status every N seconds (0 to disable)'
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    parser.add_argument(
        '--preflight',
        action='store_true',
        help='Run setup/auth checks and exit'
    )
    
    args = parser.parse_args()
    
    # Set debug logging level
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Print banner
    print("\n" + "="*60)
    print("TWITCH AI STREAM PRODUCER")
    print("="*60)
    print("Local AI feedback for streamers")
    print(f"Version: 1.0.0")
    print("="*60 + "\n")
    
    # Run calibration if requested
    if args.calibrate:
        logger.info("Running calibration wizard...")
        success = run_calibration_wizard()
        sys.exit(0 if success else 1)

    if args.preflight:
        producer = StreamProducer()
        success = producer.run_preflight()
        sys.exit(0 if success else 1)
    
    # Create producer
    producer: Optional[StreamProducer] = None

    try:
        producer = StreamProducer()
        
        # Setup signal handlers for graceful shutdown
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}")
            if producer:
                producer.stop()
            sys.exit(0)
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        # Start producer
        producer.start()
        
        # Main loop - print status periodically
        last_status_time = time.time()
        
        while True:
            time.sleep(1.0)
            
            # Print status
            if args.status_interval > 0:
                if time.time() - last_status_time >= args.status_interval:
                    producer.print_status()
                    last_status_time = time.time()
    
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        if producer:
            producer.stop()
    
    sys.exit(0)


if __name__ == "__main__":
    main()
