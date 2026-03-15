"""
LLM Provider Abstraction Layer

Defines a common interface for multiple LLM backends:
- Local: Ollama (self-hosted, no API costs)
- Cloud: OpenAI GPT (high-quality, requires API key)
- Cloud: Anthropic Claude (contextual, requires API key)

Enables adaptive routing: Choose provider based on:
- Stream resource availability (headroom)
- Quality requirements (full vs degraded)
- Cost/latency trade-offs
- Fallback chains (local → cloud → skip)

Architecture:
- Abstract provider interface (LLMProvider)
- Concrete implementations (OllamaProvider, OpenAIProvider, etc.)
- Provider registry and factory
- Request/response normalization
"""

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Generator, Tuple

logger = logging.getLogger(__name__)


class ProviderType(Enum):
    """Supported LLM provider types"""
    OLLAMA_LOCAL = "ollama"
    OPENAI_CLOUD = "openai"
    ANTHROPIC_CLOUD = "anthropic"


@dataclass
class LLMRequest:
    """Normalized inference request"""
    prompt: str
    system_prompt: Optional[str] = None
    context_tokens: int = 1024  # Available context window
    max_tokens: int = 200  # Max response length
    temperature: float = 0.7
    top_p: float = 0.95
    timeout_sec: float = 30.0  # Request timeout
    
    # Metadata for tracing
    trace_id: str = field(default_factory=lambda: "")
    streaming: bool = False  # If True, stream tokens as they arrive


@dataclass
class LLMResponse:
    """Normalized inference response"""
    text: str  # Generated text
    provider: str  # Which provider generated this
    model: str  # Which model
    finish_reason: str  # "stop", "length", "error"
    latency_sec: float  # Total time
    tokens_used: Optional[Dict[str, int]] = None  # e.g., {"prompt": 50, "completion": 30}
    error: Optional[str] = None  # If finish_reason="error"


class LLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    
    All providers must implement:
    - Health checks (alive, model_available, check_auth)
    - Inference (generate, stream_generate)
    - Cost estimation
    """
    
    def __init__(self, name: str, provider_type: ProviderType):
        self.name = name
        self.provider_type = provider_type
        self.initialized = False
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if provider is ready for inference"""
        pass
    
    @abstractmethod
    def check_credentials(self) -> bool:
        """Verify auth credentials are valid"""
        pass
    
    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Synchronous inference"""
        pass
    
    @abstractmethod
    def stream_generate(self, request: LLMRequest) -> Generator[Tuple[str, str], Any, None]:
        """Streaming inference (yields tokens)"""
        pass
    
    @abstractmethod
    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Estimate cost in USD"""
        return 0.0
    
    def __repr__(self) -> str:
        return f"{self.name}({self.provider_type.value})"


class OllamaProvider(LLMProvider):
    """
    Local Ollama provider - Self-hosted, no API costs.
    
    Best for: Always-available, low-latency, no external dependencies
    Trade-off: Requires local GPU/CPU, may be slower than cloud
    """
    
    def __init__(self, host: str = "http://localhost:11434", model: str = "qwen:8b"):
        super().__init__("Ollama", ProviderType.OLLAMA_LOCAL)
        self.host = host
        self.model = model
        self.client = None
        
        try:
            import ollama
            self.client = ollama.Client(host=host)
            self.initialized = True
            logger.info(f"OllamaProvider initialized at {host}, model={model}")
        except ImportError:
            logger.error("ollama module not found. Install with: pip install ollama")
        except Exception as e:
            logger.error(f"Failed to initialize Ollama: {e}")
    
    def is_available(self) -> bool:
        """Check Ollama is running and model is pulled"""
        if not self.initialized or not self.client:
            return False
        
        try:
            # List models to verify connection
            result = self.client.list()
            model_names = [m['name'] for m in result.get('models', [])]
            
            # Check if our model is available
            model_short = self.model.split(':')[0]
            return any(model_short in name for name in model_names)
        except Exception as e:
            logger.warning(f"Ollama availability check failed: {e}")
            return False
    
    def check_credentials(self) -> bool:
        """Ollama doesn't require credentials"""
        return self.is_available()
    
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate response using Ollama"""
        if not self.client:
            return LLMResponse(
                text="",
                provider=self.name,
                model=self.model,
                finish_reason="error",
                latency_sec=0.0,
                error="Ollama not initialized"
            )
        
        start = time.time()
        
        try:
            # Build prompt with system context
            full_prompt = request.prompt
            if request.system_prompt:
                full_prompt = f"{request.system_prompt}\n\n{request.prompt}"
            
            # Call Ollama
            response = self.client.generate(
                model=self.model,
                prompt=full_prompt,
                stream=False,
                options={
                    "temperature": request.temperature,
                    "top_p": request.top_p,
                    "num_predict": request.max_tokens,
                }
            )
            
            latency = time.time() - start
            
            return LLMResponse(
                text=response.get('response', ''),
                provider=self.name,
                model=self.model,
                finish_reason="stop",
                latency_sec=latency,
                tokens_used={
                    "prompt": response.get('prompt_eval_count', 0),
                    "completion": response.get('eval_count', 0),
                }
            )
        
        except Exception as e:
            latency = time.time() - start
            logger.error(f"Ollama generation failed: {e}")
            return LLMResponse(
                text="",
                provider=self.name,
                model=self.model,
                finish_reason="error",
                latency_sec=latency,
                error=str(e)
            )
    
    def stream_generate(self, request: LLMRequest):
        """Stream tokens from Ollama"""
        if not self.client:
            yield ("error", "Ollama not initialized")
            return
        
        try:
            full_prompt = request.prompt
            if request.system_prompt:
                full_prompt = f"{request.system_prompt}\n\n{request.prompt}"
            
            response = self.client.generate(
                model=self.model,
                prompt=full_prompt,
                stream=True,
                options={
                    "temperature": request.temperature,
                    "top_p": request.top_p,
                    "num_predict": request.max_tokens,
                }
            )
            
            for chunk in response:
                token = chunk.get('response', '')
                if token:
                    yield ('token', token)
        
        except Exception as e:
            logger.error(f"Ollama stream generation failed: {e}")
            yield ('error', str(e))
    
    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Ollama is free (self-hosted)"""
        return 0.0


class OpenAIProvider(LLMProvider):
    """
    OpenAI GPT provider - Cloud-based, high quality.
    
    Best for: High-quality responses, fallback when local unavailable
    Trade-off: API costs, requires internet, latency dependent
    """
    
    def __init__(self, api_key: Optional[str] = None, model: str = "gpt-4-turbo"):
        super().__init__("OpenAI", ProviderType.OPENAI_CLOUD)
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.model = model
        self.client = None
        
        try:
            from openai import OpenAI
            self.client = OpenAI(api_key=self.api_key)
            self.initialized = True
            logger.info(f"OpenAIProvider initialized, model={model}")
        except ImportError:
            logger.error("openai module not found. Install with: pip install openai")
        except Exception as e:
            logger.error(f"Failed to initialize OpenAI: {e}")
    
    def is_available(self) -> bool:
        """Check API key is valid"""
        if not self.initialized or not self.client or not self.api_key:
            return False
        
        try:
            # Quick check with list models
            self.client.models.list()
            return True
        except Exception:
            return False
    
    def check_credentials(self) -> bool:
        """Verify OpenAI API key"""
        return bool(self.api_key) and self.is_available()
    
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate response using OpenAI"""
        if not self.client:
            return LLMResponse(
                text="",
                provider=self.name,
                model=self.model,
                finish_reason="error",
                latency_sec=0.0,
                error="OpenAI client not initialized"
            )
        
        start = time.time()
        
        try:
            messages = []
            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})
            messages.append({"role": "user", "content": request.prompt})
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                top_p=request.top_p,
                timeout=request.timeout_sec,
            )
            
            latency = time.time() - start
            
            return LLMResponse(
                text=response.choices[0].message.content,
                provider=self.name,
                model=self.model,
                finish_reason=response.choices[0].finish_reason or "stop",
                latency_sec=latency,
                tokens_used={
                    "prompt": response.usage.prompt_tokens,
                    "completion": response.usage.completion_tokens,
                }
            )
        
        except Exception as e:
            latency = time.time() - start
            logger.error(f"OpenAI generation failed: {e}")
            return LLMResponse(
                text="",
                provider=self.name,
                model=self.model,
                finish_reason="error",
                latency_sec=latency,
                error=str(e)
            )
    
    def stream_generate(self, request: LLMRequest):
        """Stream tokens from OpenAI"""
        if not self.client:
            yield ("error", "OpenAI client not initialized")
            return
        
        try:
            messages = []
            if request.system_prompt:
                messages.append({"role": "system", "content": request.system_prompt})
            messages.append({"role": "user", "content": request.prompt})
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=request.max_tokens,
                temperature=request.temperature,
                top_p=request.top_p,
                stream=True,
                timeout=request.timeout_sec,
            )
            
            for chunk in response:
                token = chunk.choices[0].delta.content if chunk.choices[0].delta.content else ""
                if token:
                    yield ('token', token)
        
        except Exception as e:
            logger.error(f"OpenAI stream generation failed: {e}")
            yield ('error', str(e))
    
    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Rough estimate for GPT-4 turbo"""
        # GPT-4 turbo: $0.01/1K input, $0.03/1K output
        prompt_cost = (prompt_tokens / 1000) * 0.01
        completion_cost = (completion_tokens / 1000) * 0.03
        return prompt_cost + completion_cost


class ProviderRegistry:
    """
    Registry and factory for LLM providers.
    
    Manages provider lifecycle, routing, and fallback chains.
    """
    
    def __init__(self):
        self.providers: Dict[str, LLMProvider] = {}
        self.fallback_chain: List[str] = []  # Provider names in order of preference
        logger.info("ProviderRegistry initialized")
    
    def register(self, name: str, provider: LLMProvider) -> None:
        """Register a provider instance"""
        self.providers[name] = provider
        logger.info(f"Registered provider: {name} ({provider.provider_type.value})")
    
    def set_fallback_chain(self, chain: List[str]) -> None:
        """Set provider fallback order (e.g., ["ollama", "openai"])"""
        invalid = [p for p in chain if p not in self.providers]
        if invalid:
            raise ValueError(f"Unknown providers: {invalid}")
        self.fallback_chain = chain
        logger.info(f"Fallback chain set: {' → '.join(chain)}")
    
    def get_provider(self, name: str) -> Optional[LLMProvider]:
        """Get provider by name"""
        return self.providers.get(name)
    
    def get_available_provider(self, preferred_name: Optional[str] = None) -> Optional[LLMProvider]:
        """
        Get first available provider in fallback chain.
        
        If preferred_name is given and available, return that.
        Otherwise iterate fallback_chain and return first available.
        """
        if preferred_name and preferred_name in self.providers:
            provider = self.providers[preferred_name]
            if provider.is_available():
                return provider
        
        for name in self.fallback_chain:
            provider = self.providers[name]
            if provider.is_available():
                return provider
        
        logger.warning("No providers available")
        return None
    
    def list_providers(self) -> Dict[str, Dict[str, Any]]:
        """Get info on all registered providers"""
        return {
            name: {
                "type": provider.provider_type.value,
                "available": provider.is_available(),
                "initialized": provider.initialized,
            }
            for name, provider in self.providers.items()
        }


# Global registry instance
_global_registry = ProviderRegistry()


def get_global_registry() -> ProviderRegistry:
    """Get the global provider registry"""
    return _global_registry
