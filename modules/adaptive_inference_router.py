"""
Adaptive Inference Router - Intelligent producer adaptation based on stream safety

Bridges StreamSafetyManager, ProviderRegistry, and AIProducer.

Decision flow:
1. Check stream_safe() before attempting inference
2. Skip or degrade if resources insufficient
3. Route to appropriate provider based on safety level
4. Apply token constraints and timeouts
5. Fallback gracefully if primary fails
6. Log all decisions for observability

Architecture:
- Routes inference requests based on safety level
- Selects provider from registry
- Applies context/token constraints
- Implements fallback chains
- Tracks routing decisions for analytics
"""

import logging
import time
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field

from modules.stream_safety_manager import StreamSafetyManager, SafetyLevel
from modules.llm_provider import (
    ProviderRegistry,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    get_global_registry,
)

logger = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Record of a routing decision for analytics/debugging"""
    timestamp: float = field(default_factory=time.time)
    safety_level: str = ""
    stream_safe: bool = False
    selected_provider: Optional[str] = None
    context_tokens: int = 0
    response_tokens: int = 0
    skip_reason: Optional[str] = None  # If skipped, why
    fallback_used: bool = False
    latency_sec: float = 0.0
    success: bool = False


class DegradationLadder:
    """
    Deterministic degradation strategy under resource constraints.
    
    Ladder (in order of execution attempts):
    1. SAFE: Full context, full response, local+cloud available
    2. DEGRADED: Reduced context (2K), reduced response (100), prefer local
    3. MINIMAL: Minimal context (500), minimal response (50), local only
    4. UNSAFE: Skip inference entirely, preserve stream
    """
    
    # Default constraints per safety level
    CONSTRAINTS = {
        "safe": {
            "context_tokens": 8000,
            "response_tokens": 200,
            "timeout_sec": 30.0,
            "allowed_providers": ["ollama", "openai", "anthropic"],  # prefer local first
        },
        "degraded": {
            "context_tokens": 2000,
            "response_tokens": 100,
            "timeout_sec": 15.0,
            "allowed_providers": ["ollama"],  # local only (faster, no API cost)
        },
        "minimal": {
            "context_tokens": 500,
            "response_tokens": 50,
            "timeout_sec": 10.0,
            "allowed_providers": ["ollama"],  # local only
        },
        "unsafe": {
            "context_tokens": 0,
            "response_tokens": 0,
            "timeout_sec": 0.0,
            "allowed_providers": [],  # don't even try
        },
    }
    
    @classmethod
    def get_constraints(cls, safety_level: str) -> Dict:
        """Get token/timeout constraints for safety level"""
        return cls.CONSTRAINTS.get(safety_level, cls.CONSTRAINTS["unsafe"])
    
    @classmethod
    def should_attempt_inference(cls, safety_level: str) -> bool:
        """True if inference should be attempted at this level"""
        return safety_level != "unsafe"


class AdaptiveInferenceRouter:
    """
    Routes inference requests dynamically based on stream safety.
    
    Coordinates:
    - Resource monitoring (StreamSafetyManager)
    - Provider selection (ProviderRegistry)
    - Constraint application (DegradationLadder)
    - Fallback chains
    
    Usage:
        router = AdaptiveInferenceRouter(safety_mgr, provider_registry)
        router.start()
        
        # Later, in main loop:
        response = router.generate_guidance(
            prompt="...",
            system_prompt="...",
            context_data={...}
        )
        if response:
            # Safe to deliver
            tts_queue.put(response.text)
    """
    
    def __init__(
        self,
        safety_manager: Optional[StreamSafetyManager] = None,
        provider_registry: Optional[ProviderRegistry] = None,
    ):
        self.safety_manager = safety_manager or StreamSafetyManager()
        self.provider_registry = provider_registry or get_global_registry()
        
        # Routing statistics
        self.total_requests = 0
        self.successful = 0
        self.skipped = 0
        self.fallbacks = 0
        self.decisions: List[RoutingDecision] = []
        
        logger.info("AdaptiveInferenceRouter initialized")
    
    def start(self) -> None:
        """Start background monitoring"""
        self.safety_manager.start_monitoring()
        logger.info("AdaptiveInferenceRouter started")
    
    def stop(self) -> None:
        """Stop background monitoring"""
        self.safety_manager.stop_monitoring()
        logger.info("AdaptiveInferenceRouter stopped")
    
    def generate_guidance(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        context_data: Optional[Dict] = None,
    ) -> Optional[LLMResponse]:
        """
        Generate guidance with stream-safety guarantees.
        
        Implements full decision flow:
        1. Check stream_safe()
        2. Determine safety level
        3. Apply constraints
        4. Route to provider
        5. Fallback if needed
        6. Record decision
        
        Args:
            prompt: User/context prompt
            system_prompt: System context (optional)
            context_data: Diagnostic context for skip_reason
        
        Returns:
            LLMResponse if successful, None if skipped
        """
        self.total_requests += 1
        decision = RoutingDecision()
        
        # Step 1: Check if stream is safe to attempt inference
        if not self.safety_manager.stream_safe():
            self.skipped += 1
            decision.stream_safe = False
            decision.skip_reason = "stream not safe (resource constraints)"
            self.decisions.append(decision)
            logger.info(f"Skipping inference: {decision.skip_reason}")
            return None
        
        decision.stream_safe = True
        
        # Step 2: Get safety level and constraints
        safety_level = self.safety_manager.get_degradation_level()
        decision.safety_level = safety_level
        
        constraints = DegradationLadder.get_constraints(safety_level)
        decision.context_tokens = constraints["context_tokens"]
        decision.response_tokens = constraints["response_tokens"]
        
        # Step 3: Check if we should attempt at this level
        if not DegradationLadder.should_attempt_inference(safety_level):
            self.skipped += 1
            decision.skip_reason = f"safety level {safety_level} does not permit inference"
            self.decisions.append(decision)
            logger.warning(f"Inference blocked: {decision.skip_reason}")
            return None
        
        # Step 4: Build inference request with constraints
        request = LLMRequest(
            prompt=prompt[:decision.context_tokens],  # Truncate to constraint
            system_prompt=system_prompt,
            context_tokens=decision.context_tokens,
            max_tokens=decision.response_tokens,
            timeout_sec=constraints["timeout_sec"],
        )
        
        # Step 5: Select provider
        allowed_providers = constraints["allowed_providers"]
        provider = self._select_provider(allowed_providers)
        
        if not provider:
            self.skipped += 1
            decision.skip_reason = f"no available providers (tried {allowed_providers})"
            self.decisions.append(decision)
            logger.warning(f"No inference provider available: {decision.skip_reason}")
            return None
        
        decision.selected_provider = provider.name
        
        # Step 6: Attempt generation with fallback
        response = self._generate_with_fallback(provider, request, allowed_providers)
        
        if response and response.error is None:
            self.successful += 1
            decision.success = True
        else:
            self.skipped += 1
            decision.skip_reason = f"inference failed: {response.error if response else 'unknown'}"
        
        self.decisions.append(decision)
        return response
    
    def _select_provider(self, allowed_names: List[str]) -> Optional[LLMProvider]:
        """
        Select best available provider from allowed list.
        
        Returns first available provider in list order.
        """
        for name in allowed_names:
            provider = self.provider_registry.get_provider(name)
            if provider and provider.is_available():
                logger.info(f"Selected provider: {name}")
                return provider
        
        logger.warning(f"No available providers from: {allowed_names}")
        return None
    
    def _generate_with_fallback(
        self,
        primary_provider: LLMProvider,
        request: LLMRequest,
        allowed_providers: List[str],
    ) -> Optional[LLMResponse]:
        """
        Attempt generation with primary provider, fallback to others if failed.
        
        Implements fallback chain:
        1. Try primary provider
        2. If fails, try others in allowed_providers list
        3. If all fail, return failure response
        """
        providers_to_try = [primary_provider]
        
        # Add fallbacks
        for name in allowed_providers:
            if name != primary_provider.name:
                provider = self.provider_registry.get_provider(name)
                if provider:
                    providers_to_try.append(provider)
        
        last_error = None
        for provider in providers_to_try:
            try:
                logger.info(f"Attempting inference with {provider.name}...")
                response = provider.generate(request)
                
                if response.error is None:
                    if provider != primary_provider:
                        self.fallbacks += 1
                        logger.info(f"Used fallback provider: {provider.name}")
                    return response
                else:
                    last_error = response.error
                    logger.warning(f"{provider.name} failed: {response.error}")
            
            except Exception as e:
                last_error = str(e)
                logger.error(f"Exception in {provider.name}: {e}")
        
        # All providers failed
        return LLMResponse(
            text="",
            provider="unknown",
            model="unknown",
            finish_reason="error",
            latency_sec=0.0,
            error=f"All providers failed: {last_error}"
        )
    
    def get_stats(self) -> Dict:
        """Get routing statistics"""
        success_rate = (
            100.0 * self.successful / self.total_requests
            if self.total_requests > 0
            else 0.0
        )
        
        return {
            "total_requests": self.total_requests,
            "successful": self.successful,
            "skipped": self.skipped,
            "fallbacks_used": self.fallbacks,
            "success_rate": round(success_rate, 1),
            "recent_decisions": [
                {
                    "safety_level": d.safety_level,
                    "provider": d.selected_provider,
                    "success": d.success,
                    "skip_reason": d.skip_reason,
                }
                for d in self.decisions[-10:]  # Last 10 decisions
            ],
            "safety_manager_stats": self.safety_manager.get_stats(),
        }
    
    def log_metrics(self) -> None:
        """Log current metrics to logger"""
        stats = self.get_stats()
        logger.info(f"Routing stats: {stats['successful']}/{stats['total_requests']} successful, "
                   f"{stats['fallbacks_used']} fallbacks, "
                   f"{stats['success_rate']:.1f}% success rate")
