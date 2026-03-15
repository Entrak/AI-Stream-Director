"""
Non-interactive smoke tests for local test environment readiness.

Purpose:
- Verify baseline runtime/import health
- Validate critical dependencies and services
- Validate Phase 1 stream-safety components

This script avoids interactive OAuth/browser flows and hardware-heavy tests.
"""

from __future__ import annotations

import os
import sys
import time
import urllib.request
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import get_config_manager
from modules.stream_safety_manager import StreamSafetyManager
from modules.llm_provider import ProviderRegistry, OllamaProvider
from modules.adaptive_inference_router import AdaptiveInferenceRouter


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True


def _run_check(name: str, fn: Callable[[], tuple[bool, str]], required: bool = True) -> CheckResult:
    try:
        ok, detail = fn()
        return CheckResult(name=name, ok=ok, detail=detail, required=required)
    except Exception as exc:  # pragma: no cover - smoke guard
        return CheckResult(name=name, ok=False, detail=f"Exception: {exc}", required=required)


def check_python_version() -> tuple[bool, str]:
    major, minor = sys.version_info[:2]
    ok = (major, minor) >= (3, 12)
    return ok, f"Python {major}.{minor}"


def check_config_validation() -> tuple[bool, str]:
    manager = get_config_manager()
    config_ok = manager.validate()
    return config_ok, "config validation passed" if config_ok else "config validation failed"


def check_ollama_reachable() -> tuple[bool, str]:
    manager = get_config_manager()
    config = manager.get_config()
    url = f"{config.ollama_host}/api/tags"
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=6.0):
        pass
    return True, f"reachable at {config.ollama_host}"


def check_twitch_env_vars() -> tuple[bool, str]:
    client_id = bool((os.getenv("TWITCH_CLIENT_ID") or "").strip())
    client_secret = bool((os.getenv("TWITCH_CLIENT_SECRET") or "").strip())
    ok = client_id and client_secret
    return ok, "TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET present" if ok else "missing Twitch env vars"


def check_stream_safety_manager() -> tuple[bool, str]:
    manager = StreamSafetyManager()
    manager.start_monitoring()
    time.sleep(0.7)
    stats = manager.get_stats()
    manager.stop_monitoring()
    level = stats.get("safety_level", "unknown")
    cpu_available = stats.get("headroom", {}).get("cpu_available", "n/a")
    return True, f"level={level}, cpu_available={cpu_available}%"


def check_provider_registry() -> tuple[bool, str]:
    config = get_config_manager().get_config()
    registry = ProviderRegistry()
    registry.register("ollama", OllamaProvider(host=config.ollama_host, model=config.ollama_model))
    registry.set_fallback_chain(["ollama"])
    provider = registry.get_available_provider()
    ok = provider is not None
    return ok, f"selected={provider.name if provider else 'none'}"


def check_adaptive_router() -> tuple[bool, str]:
    config = get_config_manager().get_config()
    registry = ProviderRegistry()
    registry.register("ollama", OllamaProvider(host=config.ollama_host, model=config.ollama_model))
    registry.set_fallback_chain(["ollama"])
    router = AdaptiveInferenceRouter(provider_registry=registry)
    router.start()
    time.sleep(0.6)
    stats = router.get_stats()
    router.stop()
    return True, f"safety={stats.get('safety_manager_stats', {}).get('safety_level', 'unknown')}"


def main() -> int:
    checks = [
        _run_check("Python version", check_python_version, required=True),
        _run_check("Config validation", check_config_validation, required=True),
        _run_check("Ollama reachable", check_ollama_reachable, required=True),
        _run_check("Twitch credentials env", check_twitch_env_vars, required=False),
        _run_check("Stream Safety Manager", check_stream_safety_manager, required=True),
        _run_check("Provider registry", check_provider_registry, required=True),
        _run_check("Adaptive router", check_adaptive_router, required=True),
    ]

    print("\n=== TEST ENV SMOKE REPORT ===")
    for result in checks:
        status = "PASS" if result.ok else "FAIL"
        required_tag = "required" if result.required else "optional"
        print(f"[{status}] {result.name} ({required_tag})")
        if result.detail:
            print(f"  -> {result.detail}")

    required_failures = [r for r in checks if r.required and not r.ok]
    optional_failures = [r for r in checks if not r.required and not r.ok]

    print("\nSummary:")
    print(f"- Required checks failed: {len(required_failures)}")
    print(f"- Optional checks failed: {len(optional_failures)}")

    if required_failures:
        print("\nResult: NOT READY")
        return 1

    print("\nResult: READY FOR FUNCTIONAL TESTS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())