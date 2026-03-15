"""
Voice analyzer module - Real-time STT and voice analysis

Captures microphone audio and analyzes:
- Speech-to-text using faster-whisper
- Speaking pace (words per minute)
- Filler word detection
- Energy/pitch analysis

Supports both sync (threading) and async (event-driven) modes.
"""

import asyncio
import logging
import re
import time
import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional, Dict, List, Tuple
import numpy as np
import pyaudio
import librosa
from faster_whisper import WhisperModel

from config.config import get_config, AppConfig

logger = logging.getLogger(__name__)


@dataclass
class VoiceMetrics:
    """Voice analysis metrics for a time window"""
    words_per_minute: float
    filler_count: int
    total_words: int
    energy_level: float  # RMS energy (0-1 normalized)
    avg_pitch: float  # Average pitch in Hz
    duration: float  # Duration in seconds
    transcript: str
    timestamp: float


class VoiceAnalyzer:
    """
    Analyzes microphone audio in real-time
    
    Features:
    - Continuous audio capture from microphone
    - Speech-to-text using faster-whisper (GPU accelerated)
    - Speaking rate calculation
    - Filler word detection (um, uh, like, you know)
    - Energy and pitch analysis using librosa
    """
    
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        
        # Audio settings
        self.sample_rate = 16000  # Whisper uses 16kHz
        self.channels = 1  # Mono
        self.chunk_size = 1024
        
        # PyAudio instance
        self.audio: Optional[pyaudio.PyAudio] = None
        self.stream: Optional[pyaudio.Stream] = None
        
        # Whisper model (lazy loaded)
        self.whisper_model: Optional[WhisperModel] = None
        
        # Metrics history (rolling window)
        self.metrics_history: deque = deque(maxlen=10)  # Last 10 chunks
        self.lock = threading.Lock()
        
        # Filler word pattern
        self.filler_pattern = re.compile(
            r'\b(um|uh|like|you know|actually|basically|literally)\b',
            re.IGNORECASE
        )
        
        # Performance tracking
        self.total_chunks = 0
        self.total_transcription_time = 0.0
        
        # Event emission flag (for async mode)
        self._emit_events = False
        
        logger.info(f"VoiceAnalyzer initialized with chunk duration: {self.config.voice_chunk_duration}s")

    def _device_priority_score(self, device_name: str) -> int:
        """Score input devices so real mics/webcams beat remote/virtual inputs."""
        name = (device_name or "").lower()

        positive_tokens = [
            "microphone", "mic", "webcam", "camera", "usb", "headset", "yeti", "audio technica"
        ]
        negative_tokens = [
            "remote audio", "stereo mix", "what u hear", "virtual", "cable output", "output"
        ]

        score = 0
        for token in positive_tokens:
            if token in name:
                score += 10
        for token in negative_tokens:
            if token in name:
                score -= 25

        return score

    def _build_input_candidates(self, input_devices: List[Tuple[int, Dict]]) -> List[Tuple[int, Dict]]:
        """Build ordered input candidates honoring explicit config and Windows/RDP heuristics."""
        preferred_index = int(getattr(self.config, "voice_input_device_index", -1) or -1)
        explicit = []
        remaining = []

        for device_id, info in input_devices:
            if preferred_index >= 0 and device_id == preferred_index:
                explicit.append((device_id, info))
            else:
                remaining.append((device_id, info))

        # Sort remaining devices by heuristic score (higher first)
        remaining.sort(key=lambda item: self._device_priority_score(item[1].get("name", "")), reverse=True)
        return explicit + remaining

    def _try_open_input_stream(self, device_id: Optional[int], info: Optional[Dict]) -> Optional[pyaudio.Stream]:
        """Try opening a stream for a specific device with sensible rate fallbacks."""
        if self.audio is None:
            return None

        rates_to_try = [self.sample_rate]
        if info is not None:
            default_rate = int(float(info.get("defaultSampleRate", self.sample_rate) or self.sample_rate))
            if default_rate not in rates_to_try:
                rates_to_try.append(default_rate)

        for rate in rates_to_try:
            try:
                kwargs = {
                    "format": pyaudio.paInt16,
                    "channels": self.channels,
                    "rate": rate,
                    "input": True,
                    "frames_per_buffer": self.chunk_size,
                    "stream_callback": None,
                }
                if device_id is not None:
                    kwargs["input_device_index"] = device_id

                stream = self.audio.open(**kwargs)
                self.sample_rate = rate
                return stream
            except Exception:
                continue

        return None
    
    def _init_whisper(self) -> None:
        """Initialize Whisper model (lazy loaded for performance)"""
        if self.whisper_model is not None:
            return
        
        try:
            logger.info(f"Loading Whisper model: {self.config.whisper_model}")
            logger.info(f"Device: {self.config.whisper_device}, Compute type: {self.config.whisper_compute_type}")
            
            self.whisper_model = WhisperModel(
                self.config.whisper_model,
                device=self.config.whisper_device,
                compute_type=self.config.whisper_compute_type
            )
            
            logger.info("Whisper model loaded successfully")
        
        except Exception as e:
            logger.error(f"Failed to load Whisper model: {e}")
            logger.warning("Falling back to CPU mode...")
            
            try:
                self.whisper_model = WhisperModel(
                    self.config.whisper_model,
                    device="cpu",
                    compute_type="int8"
                )
                logger.info("Whisper model loaded in CPU mode")
            except Exception as e2:
                logger.error(f"Failed to load Whisper in CPU mode: {e2}")
                raise RuntimeError(f"Cannot initialize Whisper: {e2}")
    
    def _init_audio(self) -> None:
        """Initialize PyAudio and microphone stream with device fallback"""
        try:
            self.audio = pyaudio.PyAudio()
            
            # Collect available input devices
            input_devices = []
            logger.debug("Available audio input devices:")
            for i in range(self.audio.get_device_count()):
                info = self.audio.get_device_info_by_index(i)
                if float(info.get('maxInputChannels', 0) or 0) > 0:
                    logger.debug(f"  Device {i}: {info['name']}")
                    input_devices.append((i, info))
            
            if not input_devices:
                raise RuntimeError("No audio input devices found")
            
            # Try to open default input stream first
            stream_opened = False
            last_error = None
            
            try:
                default_stream = self._try_open_input_stream(None, None)
                if default_stream is not None:
                    self.stream = default_stream
                    stream_opened = True
                    logger.info(f"Audio stream initialized with default input device (rate={self.sample_rate}Hz)")
            except Exception as e:
                last_error = e
                logger.warning(f"Failed to open default input device: {e}")
            
            # If default fails, try ranked device candidates
            if not stream_opened:
                candidates = self._build_input_candidates(input_devices)
                logger.info(f"Trying {len(candidates)} alternative input devices (ranked)...")

                for device_id, device_info in candidates:
                    try:
                        candidate_stream = self._try_open_input_stream(device_id, device_info)
                        if candidate_stream is not None:
                            self.stream = candidate_stream
                            stream_opened = True
                            logger.info(
                                f"Audio stream initialized with device {device_id}: {device_info['name']} "
                                f"(rate={self.sample_rate}Hz)"
                            )
                            break
                        last_error = RuntimeError("all tested sample rates rejected")
                    except Exception as e:
                        logger.debug(f"  Device {device_id} failed: {e}")
                        last_error = e
            
            if not stream_opened:
                raise RuntimeError(f"Cannot initialize any microphone: {last_error}")
        
        except Exception as e:
            logger.error(f"Failed to initialize audio: {e}")
            raise RuntimeError(f"Cannot initialize microphone: {e}")
    
    def _capture_audio_chunk(self, duration: float) -> np.ndarray:
        """
        Capture audio chunk from microphone
        
        Args:
            duration: Duration in seconds
        
        Returns:
            Audio data as numpy array (float32, normalized to -1.0 to 1.0)
        """
        try:
            if self.stream is None:
                logger.error("Audio stream is not initialized")
                return np.array([], dtype=np.float32)

            num_frames = int(self.sample_rate * duration)
            
            # Read audio data
            audio_data = self.stream.read(num_frames, exception_on_overflow=False)
            
            # Convert to numpy array
            audio_np = np.frombuffer(audio_data, dtype=np.int16)
            
            # Normalize to float32 [-1.0, 1.0]
            audio_np = audio_np.astype(np.float32) / 32768.0

            # Resample if stream did not open at Whisper's target rate
            if self.sample_rate != 16000 and len(audio_np) > 0:
                audio_np = librosa.resample(audio_np, orig_sr=self.sample_rate, target_sr=16000)
            
            return audio_np
        
        except Exception as e:
            logger.error(f"Failed to capture audio: {e}")
            return np.array([], dtype=np.float32)
    
    def _transcribe(self, audio: np.ndarray) -> str:
        """
        Transcribe audio using Whisper
        
        Args:
            audio: Audio data (float32 numpy array)
        
        Returns:
            Transcribed text
        """
        if len(audio) == 0:
            return ""

        if self.whisper_model is None:
            logger.error("Whisper model is not initialized")
            return ""
        
        try:
            start_time = time.time()
            
            # Transcribe using faster-whisper
            segments, info = self.whisper_model.transcribe(
                audio,
                language="en",
                vad_filter=True,  # Voice activity detection (skip silence)
                beam_size=5
            )
            
            # Concatenate all segments
            transcript = " ".join([segment.text for segment in segments])
            transcript = transcript.strip()
            
            elapsed = time.time() - start_time
            self.total_transcription_time += elapsed
            
            logger.debug(f"Transcribed in {elapsed:.2f}s: {transcript[:50]}...")
            
            return transcript
        
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""
    
    def _analyze_text(self, transcript: str, duration: float) -> Dict:
        """
        Analyze transcript for speaking metrics
        
        Args:
            transcript: Transcribed text
            duration: Audio duration in seconds
        
        Returns:
            Dict with text analysis metrics
        """
        if not transcript:
            return {
                "words_per_minute": 0.0,
                "total_words": 0,
                "filler_count": 0
            }
        
        # Count words
        words = transcript.split()
        total_words = len(words)
        
        # Calculate words per minute
        words_per_minute = (total_words / duration) * 60.0 if duration > 0 else 0.0
        
        # Count filler words
        filler_matches = self.filler_pattern.findall(transcript)
        filler_count = len(filler_matches)
        
        return {
            "words_per_minute": words_per_minute,
            "total_words": total_words,
            "filler_count": filler_count
        }
    
    def _analyze_audio(self, audio: np.ndarray) -> Dict:
        """
        Analyze audio signal for energy and pitch
        
        Args:
            audio: Audio data (float32 numpy array)
        
        Returns:
            Dict with audio analysis metrics
        """
        if len(audio) == 0:
            return {
                "energy_level": 0.0,
                "avg_pitch": 0.0
            }
        
        try:
            # Calculate RMS energy
            rms = np.sqrt(np.mean(audio**2))
            
            # Normalize energy to 0-1 range (assuming typical speech RMS ~0.1)
            energy_level = min(rms / 0.1, 1.0)
            
            # Estimate pitch using librosa
            pitches, magnitudes = librosa.piptrack(
                y=audio,
                sr=self.sample_rate,
                fmin=50,  # Min frequency (Hz)
                fmax=400  # Max frequency for speech (Hz)
            )
            
            # Get average pitch (weighted by magnitude)
            pitch_values = []
            for t in range(pitches.shape[1]):
                index = magnitudes[:, t].argmax()
                pitch = pitches[index, t]
                if pitch > 0:
                    pitch_values.append(pitch)
            
            avg_pitch = np.mean(pitch_values) if pitch_values else 0.0
            
            return {
                "energy_level": float(energy_level),
                "avg_pitch": float(avg_pitch)
            }
        
        except Exception as e:
            logger.error(f"Audio analysis failed: {e}")
            return {
                "energy_level": 0.0,
                "avg_pitch": 0.0
            }
    
    def _process_chunk(self, audio: np.ndarray, duration: float) -> VoiceMetrics:
        """
        Process audio chunk: transcribe and analyze
        
        Args:
            audio: Audio data
            duration: Chunk duration
        
        Returns:
            VoiceMetrics object
        """
        # Transcribe
        transcript = self._transcribe(audio)
        
        # Analyze text
        text_metrics = self._analyze_text(transcript, duration)
        
        # Analyze audio
        audio_metrics = self._analyze_audio(audio)
        
        # Combine metrics
        metrics = VoiceMetrics(
            words_per_minute=text_metrics["words_per_minute"],
            filler_count=text_metrics["filler_count"],
            total_words=text_metrics["total_words"],
            energy_level=audio_metrics["energy_level"],
            avg_pitch=audio_metrics["avg_pitch"],
            duration=duration,
            transcript=transcript,
            timestamp=time.time()
        )
        
        return metrics
    
    def _poll_loop(self) -> None:
        """Main polling loop (runs in separate thread)"""
        logger.info("Voice analyzer polling loop started")
        
        # Initialize Whisper and audio
        try:
            self._init_whisper()
            self._init_audio()
        except Exception as e:
            logger.error(f"Voice analyzer initialization failed; voice pipeline disabled: {e}")
            self.running = False
            return
        
        while self.running:
            try:
                # Capture audio chunk
                audio = self._capture_audio_chunk(self.config.voice_chunk_duration)
                
                if len(audio) == 0:
                    logger.warning("Empty audio chunk captured")
                    continue
                
                # Process chunk
                metrics = self._process_chunk(audio, self.config.voice_chunk_duration)
                
                # Store metrics
                with self.lock:
                    self.metrics_history.append(metrics)
                    self.total_chunks += 1
                
                # Log if speech detected
                if metrics.total_words > 0:
                    logger.info(
                        f"Voice: {metrics.words_per_minute:.0f} WPM, "
                        f"{metrics.filler_count} fillers, "
                        f"energy={metrics.energy_level:.2f}"
                    )
            
            except Exception as e:
                logger.error(f"Error in voice analyzer poll loop: {e}", exc_info=True)
                time.sleep(1.0)
    
    def start(self) -> None:
        """Start the voice analyzer polling loop"""
        if self.running:
            logger.warning("Voice analyzer already running")
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logger.info("Voice analyzer started")
    
    def stop(self) -> None:
        """Stop the voice analyzer polling loop"""
        if not self.running:
            return
        
        logger.info("Stopping voice analyzer...")
        self.running = False
        
        if self.thread:
            self.thread.join(timeout=5.0)
        
        # Clean up audio
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.audio:
            self.audio.terminate()
        
        logger.info("Voice analyzer stopped")
    
    def get_recent_metrics(self, window_seconds: float = 60.0) -> List[VoiceMetrics]:
        """
        Get recent voice metrics within time window
        
        Args:
            window_seconds: Time window in seconds
        
        Returns:
            List of VoiceMetrics within window
        """
        with self.lock:
            cutoff = time.time() - window_seconds
            recent = [m for m in self.metrics_history if m.timestamp >= cutoff]
            return recent
    
    def get_average_metrics(self, window_seconds: float = 60.0) -> Dict:
        """
        Get averaged metrics over time window
        
        Args:
            window_seconds: Time window in seconds
        
        Returns:
            Dict with averaged metrics
        """
        recent = self.get_recent_metrics(window_seconds)
        
        if not recent:
            return {
                "words_per_minute": 0.0,
                "filler_count": 0,
                "energy_level": 0.0,
                "avg_pitch": 0.0,
                "total_words": 0
            }
        
        # Calculate averages
        total_words = sum(m.total_words for m in recent)
        total_duration = sum(m.duration for m in recent)
        
        return {
            "words_per_minute": (total_words / total_duration * 60.0) if total_duration > 0 else 0.0,
            "filler_count": sum(m.filler_count for m in recent),
            "energy_level": np.mean([m.energy_level for m in recent]),
            "avg_pitch": np.mean([m.avg_pitch for m in recent if m.avg_pitch > 0]),
            "total_words": total_words
        }
    
    def get_stats(self) -> Dict:
        """Get analyzer statistics"""
        with self.lock:
            avg_transcription_time = (
                self.total_transcription_time / self.total_chunks
                if self.total_chunks > 0 else 0.0
            )
            
            return {
                "total_chunks": self.total_chunks,
                "avg_transcription_time": avg_transcription_time,
                "metrics_count": len(self.metrics_history)
            }

    # ========================================================================
    # ASYNC EVENT-DRIVEN MODE (Phase 2a)
    # ========================================================================

    async def start_async(self, emit_events: bool = True) -> None:
        """
        Start async voice analyzer with event emission.
        
        Args:
            emit_events: If True, emit TRANSCRIPTION_COMPLETE events to event bus
        """
        if self.running:
            logger.warning("Voice analyzer already running")
            return

        self.running = True
        self._emit_events = emit_events
        
        # Run async polling loop
        asyncio.create_task(self._poll_loop_async())
        logger.info("Voice analyzer started (async mode)")

    async def _poll_loop_async(self) -> None:
        """Main async polling loop with event emission."""
        logger.info("Voice analyzer polling loop started (async)")
        
        # Initialize Whisper and audio
        try:
            await asyncio.get_event_loop().run_in_executor(None, self._init_whisper)
            await asyncio.get_event_loop().run_in_executor(None, self._init_audio)
        except Exception as e:
            logger.error(f"Voice analyzer initialization failed; voice pipeline disabled: {e}")
            self.running = False
            return
        
        while self.running:
            try:
                # Capture audio chunk (blocking operation, run in executor)
                audio = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._capture_audio_chunk,
                    self.config.voice_chunk_duration
                )
                
                if len(audio) == 0:
                    logger.warning("Empty audio chunk captured")
                    continue
                
                # Process chunk (CPU-intensive, run in executor)
                metrics = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._process_chunk,
                    audio,
                    self.config.voice_chunk_duration
                )
                
                # Store metrics
                with self.lock:
                    self.metrics_history.append(metrics)
                    self.total_chunks += 1
                
                # Log if speech detected
                if metrics.total_words > 0:
                    logger.info(
                        f"Voice: {metrics.words_per_minute:.0f} WPM, "
                        f"{metrics.filler_count} fillers, "
                        f"energy={metrics.energy_level:.2f}"
                    )
                
                # Emit event if in async mode
                if self._emit_events and metrics.total_words > 0:
                    await self._emit_transcription_event(metrics)
            
            except Exception as e:
                logger.error(f"Error in voice analyzer async poll loop: {e}", exc_info=True)
                await asyncio.sleep(1.0)

    async def _emit_transcription_event(self, metrics: VoiceMetrics) -> None:
        """Emit TRANSCRIPTION_COMPLETE event to event bus."""
        try:
            from core.event_bus import Event, EventType, EventPriority, get_event_bus
            
            event = Event(
                type=EventType.TRANSCRIPTION_COMPLETE,
                priority=EventPriority.NORMAL,
                data={
                    "transcript": metrics.transcript,
                    "words_per_minute": metrics.words_per_minute,
                    "filler_count": metrics.filler_count,
                    "total_words": metrics.total_words,
                    "energy_level": metrics.energy_level,
                    "avg_pitch": metrics.avg_pitch,
                    "duration": metrics.duration,
                    "timestamp": metrics.timestamp,
                },
                source="voice_analyzer"
            )
            
            bus = get_event_bus()
            await bus.publish(event)
            
        except Exception as e:
            logger.error(f"Failed to emit transcription event: {e}")

    async def stop_async(self) -> None:
        """Stop async voice analyzer."""
        if not self.running:
            return

        logger.info("Stopping voice analyzer (async)...")
        self.running = False
        
        # Give time for final events to emit
        await asyncio.sleep(0.5)
        
        # Clean up audio (blocking operations, run in executor)
        def cleanup():
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            if self.audio:
                self.audio.terminate()
        
        await asyncio.get_event_loop().run_in_executor(None, cleanup)
        logger.info("Voice analyzer stopped (async)")


if __name__ == "__main__":
    # Test voice analyzer
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    analyzer = VoiceAnalyzer()
    
    print(f"Starting voice analyzer test...")
    print(f"Chunk duration: {analyzer.config.voice_chunk_duration}s")
    print(f"Model: {analyzer.config.whisper_model}")
    print("Speak into your microphone...")
    print("Press Ctrl+C to stop\n")
    
    analyzer.start()
    
    try:
        while True:
            time.sleep(15)
            
            # Print stats every 15 seconds
            avg_metrics = analyzer.get_average_metrics(60.0)
            stats = analyzer.get_stats()
            
            print(f"\n--- Voice Metrics (60s window) ---")
            print(f"Words/min: {avg_metrics['words_per_minute']:.0f}")
            print(f"Filler count: {avg_metrics['filler_count']}")
            print(f"Energy: {avg_metrics['energy_level']:.2f}")
            print(f"Avg pitch: {avg_metrics['avg_pitch']:.0f} Hz")
            print(f"Total chunks: {stats['total_chunks']}")
            print(f"Avg transcription time: {stats['avg_transcription_time']:.2f}s")
    
    except KeyboardInterrupt:
        print("\n\nStopping...")
        analyzer.stop()
        print("Done!")
