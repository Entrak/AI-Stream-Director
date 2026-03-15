"""
Configuration management for Twitch AI Stream Producer

Handles loading, saving, and validation of user settings.
"""

import json
import logging
import os
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Dict, Any
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
load_dotenv()


@dataclass
class ChatRegion:
    """OBS chat region coordinates for OCR capture"""
    x: int
    y: int
    width: int
    height: int

    def is_valid(self) -> bool:
        """Validate region coordinates"""
        return all([
            self.x >= 0,
            self.y >= 0,
            self.width > 0,
            self.height > 0
        ])


@dataclass
class AppConfig:
    """Main application configuration"""
    
    # OBS capture settings
    obs_window_title: str = "OBS"
    chat_region: Optional[ChatRegion] = None
    chat_ingestion_mode: str = "twitch"  # twitch or ocr
    obs_websocket_enabled: bool = False
    obs_websocket_host: str = "localhost"
    obs_websocket_port: int = 4455
    obs_websocket_password: str = ""
    obs_scene_poll_interval: float = 2.0
    obs_starting_scene_patterns: list[str] = field(default_factory=lambda: ["starting", "starting soon", "intro", "countdown"])
    obs_brb_scene_patterns: list[str] = field(default_factory=lambda: ["brb", "be right back", "intermission", "break"])
    scene_extensive_feedback_enabled: bool = True
    scene_extensive_feedback_cooldown: float = 600.0
    scene_guardrail_countdown_sec: float = 3.0
    scene_auto_disable_on_disconnect: bool = True
    manual_normal_cooldown: float = 20.0
    manual_extensive_cooldown: float = 120.0
    scene_starting_cooldown: float = 900.0
    scene_brb_cooldown: float = 900.0
    starting_scene_template: str = (
        "Give a short pep-talk, then a startup checklist: mic/audio, camera/framing, game loaded, "
        "stream title/category/tags, hydration, and first chat opener."
    )
    brb_scene_template: str = (
        "Give an in-depth mid-stream review: what's working, one highest-impact fix, "
        "and two clarifying questions about goals for the next segment."
    )
    hotkey_actions: dict[str, str] = field(default_factory=lambda: {
        "F13": "manual_tip",
        "F14": "manual_extensive",
        "F15": "pause_toggle",
    })
    
    # Session management and scene mapping
    session_kickoff_enabled: bool = True
    session_history_path: str = "data/session_history.json"
    scene_to_mode_mapping: dict[str, str] = field(default_factory=dict)  # {"my_starting_scene": "starting", ...}
    session_kickoff_greeting_template: str = (
        "greet the user by their channel name with enthusiasm and warmth"
    )
    session_kickoff_goals_question_template: str = (
        "ask about stream goals and objectives for today, be conversational"
    )
    session_kickoff_pep_talk_template: str = (
        "give a motivational pep talk with practical tips and a confidence boost for the stream"
    )
    
    # Training mode (analysis-only, no real-time coaching)
    training_mode_enabled: bool = False
    training_mode_analysis_detailed: bool = True
    training_mode_auto_save_on_end: bool = True

    # Twitch chat settings (used when chat_ingestion_mode=twitch)
    twitch_channel: str = ""
    twitch_bot_username: str = ""
    twitch_oauth_token: str = ""  # format: oauth:xxxxxxxx
    twitch_auth_prefer_bot: bool = True
    twitch_anonymous_fallback: bool = True
    twitch_require_user_auth: bool = True
    twitch_redirect_uri: str = "http://localhost:8085/callback"
    twitch_stats_enabled: bool = True
    twitch_stats_poll_interval: float = 30.0
    twitch_user_access_token: str = ""
    twitch_user_refresh_token: str = ""
    twitch_user_token_expires_at: float = 0.0
    setup_completed: bool = False
    
    # Ollama settings
    ollama_model: str = "qwen3:8b"
    ollama_host: str = "http://localhost:11434"
    
    # Flask server settings
    flask_port: int = 5000
    flask_host: str = "localhost"
    
    # Performance settings
    chat_poll_interval: float = 8.0  # seconds between OCR screenshots
    voice_chunk_duration: float = 10.0  # seconds of audio to analyze
    ai_processing_interval: float = 30.0  # seconds between AI analysis
    
    # Voice analysis thresholds
    words_per_min_min: int = 100
    words_per_min_max: int = 220
    max_filler_count_per_min: int = 10
    voice_input_device_index: int = -1  # -1 means auto-select
    
    # Chat analysis thresholds
    chat_slow_threshold: int = 3  # messages per 30 seconds
    
    # Feedback settings
    feedback_cooldown: float = 60.0  # minimum seconds between TTS feedbacks
    max_feedback_words: int = 50
    in_ear_enabled: bool = True
    teleprompter_enabled: bool = True
    in_ear_cooldown: float = 60.0
    teleprompter_cooldown: float = 20.0
    teleprompter_ttl_seconds: float = 45.0
    teleprompter_max_items: int = 8
    
    # Whisper model settings
    whisper_model: str = "medium.en"  # options: tiny.en, base.en, small.en, medium.en
    whisper_device: str = "cuda"  # cuda or cpu
    whisper_compute_type: str = "float16"  # float16, int8
    
    # OCR settings
    tesseract_config: str = "--psm 6 --oem 3"
    ocr_confidence_threshold: int = 70
    
    # TTS settings
    tts_rate: int = 150  # words per minute
    tts_volume: float = 0.9  # 0.0 to 1.0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary for JSON serialization"""
        data = asdict(self)
        # Convert ChatRegion to dict if present
        if self.chat_region:
            data['chat_region'] = asdict(self.chat_region)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AppConfig':
        """Create config from dictionary"""
        # Convert chat_region dict to ChatRegion object
        if data.get('chat_region'):
            data['chat_region'] = ChatRegion(**data['chat_region'])
        return cls(**data)
    
    def is_calibrated(self) -> bool:
        """Check if chat region has been calibrated"""
        return self.chat_region is not None and self.chat_region.is_valid()


class ConfigManager:
    """Manages loading and saving configuration"""
    
    def __init__(self, config_path: str = "config/user_config.json"):
        self.config_path = Path(config_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config: AppConfig = self._load()
    
    def _load(self) -> AppConfig:
        """Load configuration from file or create default"""
        if self.config_path.exists():
            try:
                with open(self.config_path, 'r') as f:
                    data = json.load(f)
                config = AppConfig.from_dict(data)
                logger.info(f"Loaded configuration from {self.config_path}")
                return config
            except Exception as e:
                logger.error(f"Error loading config: {e}, using defaults")
                return AppConfig()
        else:
            logger.info("No existing config found, using defaults")
            return AppConfig()
    
    def save(self) -> None:
        """Save current configuration to file"""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(self.config.to_dict(), f, indent=2)
            logger.info(f"Saved configuration to {self.config_path}")
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            raise
    
    def update_chat_region(self, x: int, y: int, width: int, height: int) -> None:
        """Update chat region coordinates and save"""
        self.config.chat_region = ChatRegion(x, y, width, height)
        if not self.config.chat_region.is_valid():
            raise ValueError(f"Invalid chat region coordinates: {x}, {y}, {width}, {height}")
        self.save()
        logger.info(f"Updated chat region: x={x}, y={y}, w={width}, h={height}")
    
    def get_config(self) -> AppConfig:
        """Get current configuration"""
        return self.config
    
    def validate(self) -> bool:
        """Validate configuration and log warnings"""
        valid = True
        
        mode = (self.config.chat_ingestion_mode or "ocr").lower().strip()

        if mode not in ["twitch", "ocr"]:
            logger.warning(f"Invalid chat_ingestion_mode '{self.config.chat_ingestion_mode}', expected 'twitch' or 'ocr'")
            valid = False

        if mode == "ocr":
            if not self.config.is_calibrated():
                logger.warning("Chat region not calibrated - run with --calibrate flag")
                valid = False
        else:
            channel = (self.config.twitch_channel or "").strip()
            placeholder_channels = {"", "your_twitch_channel", "YOUR_TWITCH_CHANNEL"}

            if channel in placeholder_channels:
                if self.config.is_calibrated():
                    logger.warning("Twitch mode enabled but twitch_channel is missing/placeholder; OCR fallback will be used")
                else:
                    logger.warning("Twitch mode enabled but twitch_channel is missing/placeholder (and no OCR calibration for fallback)")
                    valid = False

        if self.config.teleprompter_max_items < 1:
            logger.warning("teleprompter_max_items must be >= 1")
            valid = False

        if self.config.teleprompter_ttl_seconds <= 0:
            logger.warning("teleprompter_ttl_seconds must be > 0")
            valid = False

        if self.config.in_ear_cooldown < 0 or self.config.teleprompter_cooldown < 0:
            logger.warning("Lane cooldown values must be >= 0")
            valid = False

        if self.config.twitch_stats_poll_interval <= 0:
            logger.warning("twitch_stats_poll_interval must be > 0")
            valid = False

        if self.config.obs_websocket_port <= 0:
            logger.warning("obs_websocket_port must be > 0")
            valid = False

        if self.config.obs_scene_poll_interval <= 0:
            logger.warning("obs_scene_poll_interval must be > 0")
            valid = False

        if self.config.scene_extensive_feedback_cooldown < 0:
            logger.warning("scene_extensive_feedback_cooldown must be >= 0")
            valid = False

        if self.config.scene_guardrail_countdown_sec < 0:
            logger.warning("scene_guardrail_countdown_sec must be >= 0")
            valid = False

        if self.config.manual_normal_cooldown < 0 or self.config.manual_extensive_cooldown < 0:
            logger.warning("manual trigger cooldowns must be >= 0")
            valid = False

        if self.config.scene_starting_cooldown < 0 or self.config.scene_brb_cooldown < 0:
            logger.warning("scene per-mode cooldowns must be >= 0")
            valid = False

        if not isinstance(self.config.hotkey_actions, dict):
            logger.warning("hotkey_actions must be a dictionary mapping key to action")
            valid = False

        if not self.config.obs_starting_scene_patterns:
            logger.warning("obs_starting_scene_patterns should contain at least one pattern")
            valid = False

        if not self.config.obs_brb_scene_patterns:
            logger.warning("obs_brb_scene_patterns should contain at least one pattern")
            valid = False

        if self.config.twitch_redirect_uri and not self.config.twitch_redirect_uri.startswith("http://"):
            logger.warning("twitch_redirect_uri must be an http:// URL for local callback handling")
            valid = False

        if self.config.voice_input_device_index < -1:
            logger.warning("voice_input_device_index must be -1 (auto) or a non-negative device index")
            valid = False
        
        if self.config.ollama_model not in ["qwen3:8b", "qwen3:14b", "qwen2:8b", "llama3", "llama3.1"]:
            logger.warning(f"Unusual Ollama model: {self.config.ollama_model}")
        
        if self.config.whisper_device == "cuda":
            try:
                import torch
                if not torch.cuda.is_available():
                    logger.warning("CUDA not available, Whisper will fall back to CPU (slow!)")
            except ImportError:
                logger.warning("PyTorch not installed, cannot verify CUDA availability")
        
        return valid


# Global config instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get or create global config manager instance"""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def get_config() -> AppConfig:
    """Convenience function to get current config"""
    return get_config_manager().get_config()


if __name__ == "__main__":
    # Test configuration system
    logging.basicConfig(level=logging.INFO)
    
    manager = ConfigManager()
    print(f"Current config: {manager.config}")
    print(f"Is calibrated: {manager.config.is_calibrated()}")
    print(f"Validation: {manager.validate()}")
    
    # Save example config
    example_path = Path("config/example_config.json")
    with open(example_path, 'w') as f:
        json.dump(AppConfig().to_dict(), f, indent=2)
    print(f"Created example config at {example_path}")
