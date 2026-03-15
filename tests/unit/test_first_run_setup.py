"""Tests for first-time user setup and preflight wiring."""

import contextlib
import types

import pytest

import main as app_main


class _FakeConfigManager:
    def __init__(self):
        self._validate_result = True
        self.config = types.SimpleNamespace(
            ollama_host="http://localhost:11434",
            setup_completed=False,
        )

    def get_config(self):
        return self.config

    def validate(self):
        return self._validate_result

    def save(self):
        return None


class _FakeTwitchStreamStats:
    def __init__(self, _config):
        self._configured = True

    def is_configured(self):
        return self._configured

    def probe_once(self, interactive_auth=True):
        return {
            "ok": True,
            "stats": {
                "is_live": False,
                "viewer_count": 0,
            },
        }


class _FakeSafetyManager:
    def get_headroom(self):
        return types.SimpleNamespace(cpu_available=65.0, memory_available=60.0)

    def assess_safety(self):
        return types.SimpleNamespace(value="safe")


def _patch_preflight_dependencies(monkeypatch):
    manager = _FakeConfigManager()

    monkeypatch.setattr(app_main, "get_config_manager", lambda: manager)
    monkeypatch.setattr(app_main, "TwitchStreamStats", _FakeTwitchStreamStats)
    monkeypatch.setattr(app_main, "StreamSafetyManager", _FakeSafetyManager)
    monkeypatch.setattr(
        app_main.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: contextlib.nullcontext(object()),
    )
    return manager


def test_first_time_preflight_progresses_from_fail_to_pass(monkeypatch):
    _patch_preflight_dependencies(monkeypatch)

    monkeypatch.delenv("TWITCH_CLIENT_ID", raising=False)
    monkeypatch.delenv("TWITCH_CLIENT_SECRET", raising=False)

    producer = app_main.StreamProducer()
    first_attempt = producer.run_preflight()
    assert first_attempt is False

    monkeypatch.setenv("TWITCH_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "fake-client-secret")

    second_attempt = producer.run_preflight()
    assert second_attempt is True


def test_first_time_preflight_fails_when_config_invalid(monkeypatch):
    manager = _patch_preflight_dependencies(monkeypatch)
    manager._validate_result = False

    monkeypatch.setenv("TWITCH_CLIENT_ID", "fake-client-id")
    monkeypatch.setenv("TWITCH_CLIENT_SECRET", "fake-client-secret")

    producer = app_main.StreamProducer()
    result = producer.run_preflight()

    assert result is False
    assert producer.config.setup_completed is False


@pytest.mark.parametrize(
    ("preflight_ok", "expected_exit"),
    [(True, 0), (False, 1)],
)
def test_main_preflight_exit_code(monkeypatch, preflight_ok, expected_exit):
    class _FakeProducer:
        def run_preflight(self):
            return preflight_ok

    monkeypatch.setattr(app_main, "StreamProducer", _FakeProducer)
    monkeypatch.setattr(app_main.sys, "argv", ["main.py", "--preflight"])

    with pytest.raises(SystemExit) as exc:
        app_main.main()

    assert exc.value.code == expected_exit
