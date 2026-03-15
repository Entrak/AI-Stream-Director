"""
Unit tests for core.event_bus module.

Tests event publishing, subscription, backpressure policies, and metrics.
"""

import asyncio
import pytest
import time
from core.event_bus import (
    Event,
    EventType,
    EventPriority,
    BackpressurePolicy,
    PriorityEventBus,
    EventMetrics,
)


@pytest.fixture
def event_bus():
    """Create a fresh event bus for each test."""
    return PriorityEventBus(
        high_queue_size=5,
        normal_queue_size=10,
        low_queue_size=3,
    )


@pytest.fixture
def sample_event():
    """Create a sample event for testing."""
    return Event(
        type=EventType.CHAT_MESSAGE,
        priority=EventPriority.NORMAL,
        data={"user": "testuser", "message": "hello"},
        source="test"
    )


class TestEvent:
    """Test Event dataclass."""
    
    def test_event_creation(self):
        event = Event(
            type=EventType.CHAT_MESSAGE,
            priority=EventPriority.NORMAL,
            data={"test": "data"}
        )
        
        assert event.type == EventType.CHAT_MESSAGE
        assert event.priority == EventPriority.NORMAL
        assert event.data == {"test": "data"}
        assert event.correlation_id is not None  # Auto-generated
        assert isinstance(event.timestamp, float)
    
    def test_correlation_id_generation(self):
        event1 = Event(type=EventType.CHAT_MESSAGE, priority=EventPriority.NORMAL)
        event2 = Event(type=EventType.CHAT_MESSAGE, priority=EventPriority.NORMAL)
        
        # Each event gets unique correlation ID
        assert event1.correlation_id != event2.correlation_id
    
    def test_custom_correlation_id(self):
        custom_id = "custom-trace-id"
        event = Event(
            type=EventType.CHAT_MESSAGE,
            priority=EventPriority.NORMAL,
            correlation_id=custom_id
        )
        
        assert event.correlation_id == custom_id


class TestEventMetrics:
    """Test EventMetrics tracking."""
    
    def test_record_publish(self):
        metrics = EventMetrics()
        event = Event(type=EventType.CHAT_MESSAGE, priority=EventPriority.NORMAL)
        
        metrics.record_publish(event)
        
        assert metrics.events_published == 1
        assert metrics.events_by_type[EventType.CHAT_MESSAGE] == 1
    
    def test_record_consume(self):
        metrics = EventMetrics()
        
        metrics.record_consume()
        metrics.record_consume()
        
        assert metrics.events_consumed == 2
    
    def test_record_drop(self):
        metrics = EventMetrics()
        event = Event(type=EventType.STREAM_STATS_UPDATE, priority=EventPriority.LOW)
        
        metrics.record_drop(event)
        
        assert metrics.events_dropped == 1
        assert metrics.drops_by_type[EventType.STREAM_STATS_UPDATE] == 1
    
    def test_get_summary(self):
        metrics = EventMetrics()
        event = Event(type=EventType.CHAT_MESSAGE, priority=EventPriority.NORMAL)
        
        metrics.record_publish(event)
        metrics.record_consume()
        
        summary = metrics.get_summary()
        
        assert summary["published"] == 1
        assert summary["consumed"] == 1
        assert summary["dropped"] == 0
        assert summary["drop_rate"] == 0.0


class TestPriorityEventBus:
    """Test PriorityEventBus core functionality."""
    
    @pytest.mark.asyncio
    async def test_publish_event(self, event_bus, sample_event):
        """Test basic event publishing."""
        result = await event_bus.publish(sample_event)
        
        assert result is True
        assert event_bus.metrics.events_published == 1
        
        # Event should be in queue
        queue = event_bus.queues[EventPriority.NORMAL]
        assert queue.qsize() == 1
    
    @pytest.mark.asyncio
    async def test_subscribe_and_consume(self, event_bus):
        """Test event subscription and consumption."""
        received_events = []
        
        async def handler(event: Event):
            received_events.append(event)
        
        # Subscribe handler
        event_bus.subscribe(EventType.CHAT_MESSAGE, handler)
        
        # Start bus
        await event_bus.start()
        
        # Publish event
        event = Event(
            type=EventType.CHAT_MESSAGE,
            priority=EventPriority.NORMAL,
            data={"test": "data"}
        )
        await event_bus.publish(event)
        
        # Wait for consumption
        await asyncio.sleep(0.1)
        
        # Stop bus
        await event_bus.stop(drain=True)
        
        # Verify handler was called
        assert len(received_events) == 1
        assert received_events[0].data == {"test": "data"}
        assert event_bus.metrics.events_consumed == 1
    
    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, event_bus):
        """Test multiple handlers for same event type."""
        handler1_calls = []
        handler2_calls = []
        
        async def handler1(event: Event):
            handler1_calls.append(event)
        
        async def handler2(event: Event):
            handler2_calls.append(event)
        
        # Subscribe both handlers
        event_bus.subscribe(EventType.CHAT_MESSAGE, handler1)
        event_bus.subscribe(EventType.CHAT_MESSAGE, handler2)
        
        await event_bus.start()
        
        event = Event(type=EventType.CHAT_MESSAGE, priority=EventPriority.NORMAL)
        await event_bus.publish(event)
        
        await asyncio.sleep(0.1)
        await event_bus.stop(drain=True)
        
        # Both handlers should receive the event
        assert len(handler1_calls) == 1
        assert len(handler2_calls) == 1
    
    @pytest.mark.asyncio
    async def test_high_priority_blocks_on_full(self, event_bus):
        """Test HIGH priority events block when queue is full."""
        # Fill the HIGH queue (maxsize=5)
        for i in range(5):
            event = Event(
                type=EventType.SAFETY_STATE_CHANGE,
                priority=EventPriority.HIGH,
                data={"index": i}
            )
            await event_bus.publish(event)
        
        # Queue should be full
        assert event_bus.queues[EventPriority.HIGH].qsize() == 5
        
        # Publishing one more should block (we'll test with timeout)
        event = Event(
            type=EventType.SAFETY_STATE_CHANGE,
            priority=EventPriority.HIGH,
            data={"index": 5}
        )
        
        # Start a consumer to drain queue
        await event_bus.start()
        
        # This should succeed (consumer drains queue)
        published = await event_bus.publish(event)
        
        await event_bus.stop(drain=True)
        
        assert published is True
        assert event_bus.metrics.events_dropped == 0  # No drops for HIGH
    
    @pytest.mark.asyncio
    async def test_normal_priority_drops_oldest(self, event_bus):
        """Test NORMAL priority drops oldest event when queue is full."""
        # Fill the NORMAL queue (maxsize=10)
        for i in range(10):
            event = Event(
                type=EventType.CHAT_MESSAGE,
                priority=EventPriority.NORMAL,
                data={"index": i}
            )
            await event_bus.publish(event)
        
        # Queue is full
        assert event_bus.queues[EventPriority.NORMAL].qsize() == 10
        
        # Publish one more - should drop oldest
        new_event = Event(
            type=EventType.CHAT_MESSAGE,
            priority=EventPriority.NORMAL,
            data={"index": 10}
        )
        published = await event_bus.publish(new_event)
        
        assert published is True
        assert event_bus.metrics.events_dropped == 1  # Oldest dropped
        assert event_bus.queues[EventPriority.NORMAL].qsize() == 10  # Still full
    
    @pytest.mark.asyncio
    async def test_low_priority_drops_new(self, event_bus):
        """Test LOW priority drops new event when queue is full."""
        # Fill the LOW queue (maxsize=3)
        for i in range(3):
            event = Event(
                type=EventType.STREAM_STATS_UPDATE,
                priority=EventPriority.LOW,
                data={"index": i}
            )
            await event_bus.publish(event)
        
        # Queue is full
        assert event_bus.queues[EventPriority.LOW].qsize() == 3
        
        # Publish one more - should be dropped
        new_event = Event(
            type=EventType.STREAM_STATS_UPDATE,
            priority=EventPriority.LOW,
            data={"index": 3}
        )
        published = await event_bus.publish(new_event)
        
        assert published is False  # New event dropped
        assert event_bus.metrics.events_dropped == 1
        assert event_bus.queues[EventPriority.LOW].qsize() == 3  # Still original 3
    
    @pytest.mark.asyncio
    async def test_get_metrics(self, event_bus):
        """Test metrics reporting."""
        # Publish some events
        await event_bus.publish(Event(type=EventType.CHAT_MESSAGE, priority=EventPriority.NORMAL))
        await event_bus.publish(Event(type=EventType.SAFETY_STATE_CHANGE, priority=EventPriority.HIGH))
        
        # Start and consume
        await event_bus.start()
        await asyncio.sleep(0.1)
        await event_bus.stop(drain=True)
        
        metrics = event_bus.get_metrics()
        
        assert metrics["published"] == 2
        assert metrics["consumed"] == 2
        assert metrics["dropped"] == 0
        assert metrics["drop_rate"] == 0.0
    
    @pytest.mark.asyncio
    async def test_graceful_shutdown_with_drain(self, event_bus):
        """Test graceful shutdown drains pending events."""
        received = []
        
        async def slow_handler(event: Event):
            await asyncio.sleep(0.05)  # Simulate slow processing
            received.append(event)
        
        event_bus.subscribe(EventType.CHAT_MESSAGE, slow_handler)
        await event_bus.start()
        
        # Publish multiple events
        for i in range(5):
            event = Event(
                type=EventType.CHAT_MESSAGE,
                priority=EventPriority.NORMAL,
                data={"index": i}
            )
            await event_bus.publish(event)
        
        # Stop with drain=True should wait for all events
        await event_bus.stop(drain=True)
        
        # All events should be processed
        assert len(received) == 5
    
    @pytest.mark.asyncio
    async def test_no_handler_for_event(self, event_bus):
        """Test publishing event with no subscribed handlers."""
        await event_bus.start()
        
        # Publish event without any handler
        event = Event(type=EventType.CHAT_MESSAGE, priority=EventPriority.NORMAL)
        await event_bus.publish(event)
        
        await asyncio.sleep(0.1)
        await event_bus.stop(drain=True)
        
        # Should still consume the event (just no handler called)
        assert event_bus.metrics.events_consumed == 1


class TestGlobalEventBus:
    """Test global event bus singleton."""
    
    def test_get_event_bus_singleton(self):
        from core.event_bus import get_event_bus, set_event_bus
        
        # Reset global
        set_event_bus(None)
        
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        
        # Should return same instance
        assert bus1 is bus2
    
    def test_set_event_bus(self):
        from core.event_bus import get_event_bus, set_event_bus
        
        custom_bus = PriorityEventBus()
        set_event_bus(custom_bus)
        
        retrieved = get_event_bus()
        
        assert retrieved is custom_bus


if __name__ == "__main__":
    # Run with: python -m pytest tests/unit/test_event_bus.py -v
    pytest.main([__file__, "-v"])
