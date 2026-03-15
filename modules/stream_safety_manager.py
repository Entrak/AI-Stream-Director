"""
Stream Safety Manager - Ensures assistant never degrades stream quality

Monitors system resources and enforces hard guardrails to prevent interference
with streaming pipeline (FPS, encoder, audio continuity).

Architecture:
- Real-time CPU/GPU/VRAM headroom tracking
- Adaptive inference capacity determination
- Deterministic degradation ladder
- stream_safe() check before any model inference

Supports both sync (threading) and async (event-driven) modes.
"""

import asyncio
import logging
import psutil
import threading
import time
from typing import Dict, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SafetyLevel(Enum):
    """Inference safety classification"""
    SAFE = "safe"  # High headroom, full capability
    DEGRADED = "degraded"  # Medium headroom, lightweight inference
    MINIMAL = "minimal"  # Low headroom, only critical features
    UNSAFE = "unsafe"  # Insufficient resources, skip inference


@dataclass
class ResourceHeadroom:
    """Snapshot of system resource availability"""
    cpu_percent: float  # 0-100, currently in use
    cpu_available: float  # 0-100, available for assistant
    gpu_percent: Optional[float]  # None if no GPU or NVIDIA unavailable
    gpu_available: Optional[float]
    vram_percent: Optional[float]
    vram_available: Optional[float]  # MB
    memory_percent: float  # System RAM in use
    memory_available: float  # 0-100
    
    timestamp: float = field(default_factory=time.time)
    
    @property
    def has_gpu(self) -> bool:
        """True if GPU monitoring is available"""
        return self.gpu_percent is not None


@dataclass
class SafetyPolicy:
    """Resource thresholds and constraints"""
    # Hard guardrails - assistant yield if exceeded
    cpu_max_percent: float = 75.0  # Don't exceed 75% CPU duty
    gpu_max_percent: float = 85.0  # Don't exceed 85% GPU duty
    vram_max_percent: float = 80.0  # Don't exceed 80% VRAM
    memory_max_percent: float = 85.0  # Don't exceed 85% system RAM
    
    # Headroom thresholds for capacity determination
    cpu_safe_threshold: float = 50.0  # >50% available = full capacity
    cpu_degraded_threshold: float = 25.0  # >25% available = lightweight
    
    gpu_safe_threshold: float = 30.0  # >30% available = full capacity
    gpu_degraded_threshold: float = 15.0  # >15% available = lightweight
    
    vram_safe_threshold: float = 30.0  # >30% available = full capacity
    vram_degraded_threshold: float = 15.0  # >15% available = lightweight
    
    # Feature constraints
    max_context_tokens: Dict[str, int] = field(default_factory=lambda: {
        "safe": 8000,
        "degraded": 2000,
        "minimal": 500,
    })
    
    max_response_tokens: Dict[str, int] = field(default_factory=lambda: {
        "safe": 200,
        "degraded": 100,
        "minimal": 50,
    })


class StreamSafetyManager:
    """
    Ensures stream quality never degrades due to assistant resource usage.
    
    Continuously monitors CPU/GPU/VRAM headroom and enforces constraints before
    any assistant inference. Uses degradation ladder to reduce feature richness
    rather than blocking entirely.
    
    Usage:
        safety = StreamSafetyManager()
        headroom = safety.get_headroom()
        level = safety.assess_safety()
        
        if safety.stream_safe():
            # Safe to run inference
            guidance = ai_producer.generate(context, degradation_level=level)
        else:
            # Skip inference, preserve stream
            pass
    """
    
    def __init__(self, policy: Optional[SafetyPolicy] = None):
        self.policy = policy or SafetyPolicy()
        self._headroom: Optional[ResourceHeadroom] = None
        self._headroom_lock = threading.Lock()
        
        # Monitor thread
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitoring = False
        
        # Stats
        self._safety_checks = 0
        self._unsafe_count = 0
        self._degraded_count = 0
        
        # Event emission (for async mode)
        self._emit_events = False
        self._last_safety_level: Optional[SafetyLevel] = None
        
        logger.info("StreamSafetyManager initialized")
    
    def start_monitoring(self) -> None:
        """Start background resource monitoring thread"""
        if self._monitoring:
            logger.warning("Monitoring already running")
            return
        
        self._monitoring = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="StreamSafetyMonitor"
        )
        self._monitor_thread.start()
        logger.info("Resource monitoring started")
    
    def stop_monitoring(self) -> None:
        """Stop background monitoring"""
        self._monitoring = False
        if self._monitor_thread:
            self._monitor_thread.join(timeout=2.0)
        logger.info("Resource monitoring stopped")
    
    def _monitor_loop(self) -> None:
        """Background thread: update headroom snapshot every 500ms"""
        while self._monitoring:
            try:
                headroom = self._sample_resources()
                with self._headroom_lock:
                    self._headroom = headroom
                
                time.sleep(0.5)  # 500ms sample interval
            except Exception as e:
                logger.error(f"Resource monitoring error: {e}")
                time.sleep(1.0)
    
    def _sample_resources(self) -> ResourceHeadroom:
        """Collect current CPU/GPU/VRAM snapshot"""
        # CPU
        cpu_percent = psutil.cpu_percent(interval=0.1)
        cpu_available = 100.0 - cpu_percent
        
        # GPU (try NVIDIA, graceful fallback if unavailable)
        gpu_percent, gpu_available = self._get_gpu_status()
        
        # VRAM (if GPU available)
        vram_percent, vram_available = self._get_vram_status()
        
        # System RAM
        mem = psutil.virtual_memory()
        memory_percent = mem.percent
        memory_available = 100.0 - memory_percent
        
        return ResourceHeadroom(
            cpu_percent=cpu_percent,
            cpu_available=cpu_available,
            gpu_percent=gpu_percent,
            gpu_available=gpu_available,
            vram_percent=vram_percent,
            vram_available=vram_available,
            memory_percent=memory_percent,
            memory_available=memory_available,
        )
    
    def _get_gpu_status(self) -> Tuple[Optional[float], Optional[float]]:
        """Get NVIDIA GPU utilization, or (None, None) if unavailable"""
        try:
            import pynvml
            
            if not pynvml.nvmlIsInitialized():
                pynvml.nvmlInit()
            
            # Query first GPU
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            utilization = pynvml.nvmlDeviceGetUtilizationRates(handle)
            
            gpu_percent = float(utilization.gpu)
            gpu_available = 100.0 - gpu_percent
            
            return gpu_percent, gpu_available
        except Exception:
            # GPU unavailable (no NVIDIA, no pynvml, etc.)
            return None, None
    
    def _get_vram_status(self) -> Tuple[Optional[float], Optional[float]]:
        """Get NVIDIA VRAM usage, or (None, None) if unavailable"""
        try:
            import pynvml
            
            if not pynvml.nvmlIsInitialized():
                pynvml.nvmlInit()
            
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
            
            total = mem_info.total / (1024 ** 3)  # Convert to GB
            used = mem_info.used / (1024 ** 3)
            vram_percent = 100.0 * (used / total) if total > 0 else 0.0
            vram_available = total - used  # MB remaining
            
            return vram_percent, vram_available
        except Exception:
            return None, None
    
    def get_headroom(self) -> ResourceHeadroom:
        """Get latest resource headroom snapshot"""
        with self._headroom_lock:
            if self._headroom is None:
                # If monitoring hasn't started, sample once
                return self._sample_resources()
            return self._headroom
    
    def assess_safety(self) -> SafetyLevel:
        """
        Determine safe inference capacity based on current resources.
        
        Returns:
            SafetyLevel.SAFE: Full inference capability
            SafetyLevel.DEGRADED: Reduced context/tokens
            SafetyLevel.MINIMAL: Only critical features
            SafetyLevel.UNSAFE: Skip inference entirely
        """
        headroom = self.get_headroom()
        
        # Hard constraints: if exceeded, unsafe (respect streaming pipeline)
        if (headroom.cpu_percent > self.policy.cpu_max_percent or
            headroom.memory_percent > self.policy.memory_max_percent):
            return SafetyLevel.UNSAFE
        
        if headroom.has_gpu:
            if (headroom.gpu_percent and headroom.gpu_percent > self.policy.gpu_max_percent or
                headroom.vram_percent and headroom.vram_percent > self.policy.vram_max_percent):
                return SafetyLevel.UNSAFE
        
        # Soft thresholds: determine inference capacity
        cpu_ok = headroom.cpu_available >= self.policy.cpu_safe_threshold
        mem_ok = headroom.memory_available >= 25.0  # 25% RAM available
        
        gpu_ok = True
        if headroom.has_gpu:
            gpu_ok = (headroom.gpu_available and 
                     headroom.gpu_available >= self.policy.gpu_safe_threshold)
        
        if cpu_ok and mem_ok and gpu_ok:
            return SafetyLevel.SAFE
        
        # Check degraded threshold
        cpu_degraded = headroom.cpu_available >= self.policy.cpu_degraded_threshold
        mem_degraded = headroom.memory_available >= 15.0
        
        gpu_degraded = True
        if headroom.has_gpu:
            gpu_degraded = (headroom.gpu_available and
                          headroom.gpu_available >= self.policy.gpu_degraded_threshold)
        
        if cpu_degraded and mem_degraded and gpu_degraded:
            return SafetyLevel.DEGRADED
        
        # Minimal capacity check
        if headroom.cpu_available >= 10.0 and headroom.memory_available >= 10.0:
            return SafetyLevel.MINIMAL
        
        return SafetyLevel.UNSAFE
    
    def stream_safe(self) -> bool:
        """
        Quick check: is it safe to attempt inference right now?
        
        Returns:
            True if not at risk of stream degradation
            False if inference would likely impact stream quality
        """
        self._safety_checks += 1
        
        level = self.assess_safety()
        if level == SafetyLevel.UNSAFE:
            self._unsafe_count += 1
            return False
        
        if level == SafetyLevel.MINIMAL:
            self._degraded_count += 1
        
        return True  # SAFE or DEGRADED are both inference-capable
    
    def get_degradation_level(self) -> str:
        """
        Get the current degradation level name.
        
        Returns:
            "safe", "degraded", "minimal", or "unsafe"
        """
        level = self.assess_safety()
        return level.value
    
    def get_inference_constraints(self) -> Dict[str, int]:
        """
        Get token/context limits appropriate for current safety level.
        
        Returns:
            Dict with 'max_context_tokens' and 'max_response_tokens'
        """
        level = self.get_degradation_level()
        
        return {
            "max_context_tokens": self.policy.max_context_tokens.get(level, 500),
            "max_response_tokens": self.policy.max_response_tokens.get(level, 50),
        }
    
    def get_stats(self) -> Dict:
        """Get monitoring statistics"""
        headroom = self.get_headroom()
        level = self.assess_safety()
        
        return {
            "monitoring_active": self._monitoring,
            "safety_level": level.value,
            "checks_total": self._safety_checks,
            "unsafe_triggers": self._unsafe_count,
            "degraded_count": self._degraded_count,
            "headroom": {
                "cpu_available": round(headroom.cpu_available, 1),
                "cpu_used": round(headroom.cpu_percent, 1),
                "gpu_available": round(headroom.gpu_available, 1) if headroom.gpu_available else None,
                "gpu_used": round(headroom.gpu_percent, 1) if headroom.gpu_percent else None,
                "vram_available": round(headroom.vram_available, 1) if headroom.vram_available else None,
                "memory_available": round(headroom.memory_available, 1),
                "memory_used": round(headroom.memory_percent, 1),
            },
            "constraints": self.get_inference_constraints(),
        }

    # ========================================================================
    # ASYNC EVENT-DRIVEN MODE (Phase 2a)
    # ========================================================================

    async def start_monitoring_async(self, emit_events: bool = True) -> None:
        """
        Start async resource monitoring with event emission.
        
        Args:
            emit_events: If True, emit SAFETY_STATE_CHANGE events on level changes
        """
        if self._monitoring:
            logger.warning("Monitoring already running")
            return

        self._monitoring = True
        self._emit_events = emit_events
        
        # Run async monitoring loop
        asyncio.create_task(self._monitor_loop_async())
        logger.info("Resource monitoring started (async mode)")

    async def _monitor_loop_async(self) -> None:
        """Async monitoring loop with event emission on state changes."""
        while self._monitoring:
            try:
                # Sample resources (blocking operation, run in executor)
                headroom = await asyncio.get_event_loop().run_in_executor(
                    None,
                    self._sample_resources
                )
                
                with self._headroom_lock:
                    self._headroom = headroom
                
                # Check if safety level changed
                if self._emit_events:
                    current_level = self.assess_safety()
                    if current_level != self._last_safety_level:
                        await self._emit_safety_event(current_level, headroom)
                        self._last_safety_level = current_level
                
                await asyncio.sleep(0.5)  # 500ms sample interval
                
            except Exception as e:
                logger.error(f"Resource monitoring error (async): {e}")
                await asyncio.sleep(1.0)

    async def _emit_safety_event(self, level: SafetyLevel, headroom: ResourceHeadroom) -> None:
        """Emit SAFETY_STATE_CHANGE event to event bus."""
        try:
            from core.event_bus import Event, EventType, EventPriority, get_event_bus
            
            event = Event(
                type=EventType.SAFETY_STATE_CHANGE,
                priority=EventPriority.HIGH,  # Safety changes are critical
                data={
                    "safety_level": level.value,
                    "cpu_available": headroom.cpu_available,
                    "cpu_percent": headroom.cpu_percent,
                    "gpu_available": headroom.gpu_available,
                    "gpu_percent": headroom.gpu_percent,
                    "vram_available": headroom.vram_available,
                    "vram_percent": headroom.vram_percent,
                    "memory_available": headroom.memory_available,
                    "memory_percent": headroom.memory_percent,
                    "constraints": self.get_inference_constraints(),
                },
                source="stream_safety_manager"
            )
            
            bus = get_event_bus()
            await bus.publish(event)
            
            logger.info(f"Safety level changed to {level.value}")
            
        except Exception as e:
            logger.error(f"Failed to emit safety event: {e}")

    async def stop_monitoring_async(self) -> None:
        """Stop async monitoring."""
        if not self._monitoring:
            return

        logger.info("Stopping resource monitoring (async)...")
        self._monitoring = False
        
        # Give time for final events to emit
        await asyncio.sleep(0.5)
        
        logger.info("Resource monitoring stopped (async)")

