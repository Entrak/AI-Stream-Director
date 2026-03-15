"""
Event Bus - Async event distribution with bounded queues and backpressure.

Provides priority-based event routing with configurable backpressure policies.
Replaces polling-based architecture with event-driven async/await patterns.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Coroutine, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


class EventPriority(Enum):
    """Event priority levels for queue management."""
    HIGH = 1      # Safety state changes, critical errors - never drop
    NORMAL = 2    # Chat messages, voice transcriptions - drop oldest on overflow
    LOW = 3       # Stream stats, metrics updates - drop immediately on overflow


class EventType(Enum):
    """Event types for routing and filtering."""
    # Input events (producers)
    CHAT_MESSAGE = "chat_message"
    TRANSCRIPTION_COMPLETE = "transcription_complete"
    SAFETY_STATE_CHANGE = "safety_state_change"
    STREAM_STATS_UPDATE = "stream_stats_update"
    
    # Processing events (internal)
    GUIDANCE_TRIGGERED = "guidance_triggered"
    INFERENCE_REQUESTED = "inference_requested"
    INFERENCE_COMPLETE = "inference_complete"
    INFERENCE_FAILED = "inference_failed"
    
    # Output events (delivery)
    TTS_REQUESTED = "tts_requested"
    TTS_COMPLETE = "tts_complete"
    TELEPROMPTER_UPDATED = "teleprompter_updated"
    
    # Control events
    SHUTDOWN = "shutdown"


class BackpressurePolicy(Enum):
    """How to handle queue overflow."""
    BLOCK = "block"         # Block producer until space available (HIGH priority)
    DROP_OLDEST = "drop_oldest"  # Drop oldest event in queue (NORMAL priority)
    DROP_NEW = "drop_new"   # Drop the new event (LOW priority)


@dataclass
class Event:
    """Base event structure for all event types."""
    type: EventType
    priority: EventPriority
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)
    correlation_id: Optional[str] = None  # For tracing event chains
    source: Optional[str] = None  # Component that emitted the event
    
    def __post_init__(self):
        if self.correlation_id is None:
            self.correlation_id = uuid4().hex


@dataclass
class EventMetrics:
    """Metrics for monitoring event bus health."""
    events_published: int = 0
    events_consumed: int = 0
    events_dropped: int = 0
    events_by_type: dict[EventType, int] = field(default_factory=dict)
    drops_by_type: dict[EventType, int] = field(default_factory=dict)
    queue_depths: dict[EventPriority, int] = field(default_factory=dict)
    
    def record_publish(self, event: Event) -> None:
        self.events_published += 1
        self.events_by_type[event.type] = self.events_by_type.get(event.type, 0) + 1
    
    def record_consume(self) -> None:
        self.events_consumed += 1
    
    def record_drop(self, event: Event) -> None:
        self.events_dropped += 1
        self.drops_by_type[event.type] = self.drops_by_type.get(event.type, 0) + 1
    
    def get_summary(self) -> dict[str, Any]:
        return {
            "published": self.events_published,
            "consumed": self.events_consumed,
            "dropped": self.events_dropped,
            "drop_rate": self.events_dropped / max(self.events_published, 1),
            "queue_depths": {p.name: d for p, d in self.queue_depths.items()},
        }


EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class PriorityEventBus:
    """
    Async event bus with priority queues and backpressure.
    
    Features:
    - Separate bounded queues per priority level
    - Configurable backpressure policies
    - Subscriber pattern for event consumers
    - Metrics collection for monitoring
    """
    
    def __init__(
        self,
        high_queue_size: int = 10,
        normal_queue_size: int = 100,
        low_queue_size: int = 20,
    ):
        # Priority-based queues with size limits
        self.queues: dict[EventPriority, asyncio.Queue] = {
            EventPriority.HIGH: asyncio.Queue(maxsize=high_queue_size),
            EventPriority.NORMAL: asyncio.Queue(maxsize=normal_queue_size),
            EventPriority.LOW: asyncio.Queue(maxsize=low_queue_size),
        }
        
        # Backpressure policies per priority
        self.backpressure_policies: dict[EventPriority, BackpressurePolicy] = {
            EventPriority.HIGH: BackpressurePolicy.BLOCK,
            EventPriority.NORMAL: BackpressurePolicy.DROP_OLDEST,
            EventPriority.LOW: BackpressurePolicy.DROP_NEW,
        }
        
        # Event handlers by type
        self.handlers: dict[EventType, list[EventHandler]] = {}
        
        # Metrics
        self.metrics = EventMetrics()
        
        # Control
        self.running = False
        self.consumer_tasks: list[asyncio.Task] = []
        
        logger.info(
            f"EventBus initialized: HIGH={high_queue_size}, "
            f"NORMAL={normal_queue_size}, LOW={low_queue_size}"
        )
    
    async def publish(self, event: Event) -> bool:
        """
        Publish event to appropriate priority queue.
        
        Returns True if event was queued, False if dropped due to backpressure.
        """
        queue = self.queues[event.priority]
        policy = self.backpressure_policies[event.priority]
        
        try:
            # Try to put without blocking first
            queue.put_nowait(event)
            self.metrics.record_publish(event)
            logger.debug(f"Published {event.type.value} (priority={event.priority.name})")
            return True
            
        except asyncio.QueueFull:
            # Apply backpressure policy
            if policy == BackpressurePolicy.BLOCK:
                # Block until space available (only for HIGH priority)
                logger.warning(f"HIGH priority queue full, blocking...")
                await queue.put(event)
                self.metrics.record_publish(event)
                return True
                
            elif policy == BackpressurePolicy.DROP_OLDEST:
                # Drop oldest event, insert new one
                try:
                    dropped = queue.get_nowait()
                    self.metrics.record_drop(dropped)
                    logger.warning(f"Dropped oldest {dropped.type.value} due to backpressure")
                except asyncio.QueueEmpty:
                    pass
                
                queue.put_nowait(event)
                self.metrics.record_publish(event)
                return True
                
            elif policy == BackpressurePolicy.DROP_NEW:
                # Drop the new event
                self.metrics.record_drop(event)
                logger.warning(f"Dropped new {event.type.value} due to backpressure")
                return False
            
            # Fallback (should never reach here)
            logger.error(f"Unknown backpressure policy: {policy}")
            return False
        
        except Exception as e:
            logger.error(f"Failed to publish event: {e}")
            return False
    
    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Register an async handler for specific event type."""
        if event_type not in self.handlers:
            self.handlers[event_type] = []
        self.handlers[event_type].append(handler)
        logger.info(f"Subscribed handler to {event_type.value}")
    
    async def _consume_queue(self, priority: EventPriority) -> None:
        """Consumer coroutine for a specific priority queue."""
        queue = self.queues[priority]
        
        while True:
            try:
                # Wait for next event
                event = await queue.get()
                self.metrics.record_consume()
                
                # Update queue depth metric
                self.metrics.queue_depths[priority] = queue.qsize()
                
                # Dispatch to handlers
                handlers = self.handlers.get(event.type, [])
                if handlers:
                    # Run handlers concurrently
                    await asyncio.gather(
                        *[handler(event) for handler in handlers],
                        return_exceptions=True
                    )
                else:
                    logger.debug(f"No handlers for {event.type.value}")
                
                queue.task_done()
                
            except asyncio.CancelledError:
                logger.info(f"Consumer for {priority.name} queue cancelled")
                break
            except Exception as e:
                logger.error(f"Error consuming {priority.name} queue: {e}", exc_info=True)
    
    async def start(self) -> None:
        """Start event bus consumers."""
        if self.running:
            logger.warning("EventBus already running")
            return
        
        self.running = True
        logger.info("Starting EventBus consumers...")
        
        # Start consumer for each priority queue
        for priority in EventPriority:
            task = asyncio.create_task(
                self._consume_queue(priority),
                name=f"consumer_{priority.name}"
            )
            self.consumer_tasks.append(task)
        
        logger.info(f"Started {len(self.consumer_tasks)} consumer tasks")
    
    async def stop(self, drain: bool = True) -> None:
        """
        Stop event bus and optionally drain pending events.
        
        Args:
            drain: If True, wait for all pending events to be processed
        """
        if not self.running:
            return
        
        logger.info("Stopping EventBus...")
        self.running = False
        
        if drain:
            # Wait for all queues to empty
            for priority, queue in self.queues.items():
                if queue.qsize() > 0:
                    logger.info(f"Draining {queue.qsize()} events from {priority.name} queue...")
                    await queue.join()
        
        # Cancel consumer tasks
        for task in self.consumer_tasks:
            task.cancel()
        
        # Wait for cancellation to complete
        await asyncio.gather(*self.consumer_tasks, return_exceptions=True)
        self.consumer_tasks.clear()
        
        logger.info("EventBus stopped")
    
    def get_metrics(self) -> dict[str, Any]:
        """Get current event bus metrics."""
        # Update queue depth metrics
        for priority, queue in self.queues.items():
            self.metrics.queue_depths[priority] = queue.qsize()
        
        return self.metrics.get_summary()


# Global event bus instance (singleton pattern)
_global_event_bus: Optional[PriorityEventBus] = None


def get_event_bus() -> PriorityEventBus:
    """Get or create the global event bus instance."""
    global _global_event_bus
    if _global_event_bus is None:
        _global_event_bus = PriorityEventBus()
    return _global_event_bus


def set_event_bus(bus: Optional[PriorityEventBus]) -> None:
    """Set the global event bus instance (for testing)."""
    global _global_event_bus
    _global_event_bus = bus
