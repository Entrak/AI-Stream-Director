"""
OBS scene watcher via obs-websocket (optional).

Polls current program scene and invokes callback on scene changes.
"""

import asyncio
import importlib
import logging
import time
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


class ObsSceneWatcher:
    """Optional OBS scene watcher based on obs-websocket API."""

    def __init__(
        self,
        host: str,
        port: int,
        password: str,
        poll_interval: float = 2.0,
    ):
        self.host = host
        self.port = port
        self.password = password
        self.poll_interval = poll_interval

        self.running = False
        self._task: Optional[asyncio.Task] = None
        self._last_scene: Optional[str] = None

        self.connected = False
        self.connection_error: Optional[str] = None
        self.last_attempt_ts: float = 0.0
        self.last_connected_ts: float = 0.0
        self.next_retry_at: float = 0.0
        self.retry_backoff_sec: float = 1.0
        self.max_retry_backoff_sec: float = 30.0
        self.reconnect_attempts: int = 0

    async def start(self, on_scene_change: Callable[[str], Awaitable[None]]) -> None:
        """Start scene polling in background."""
        if self.running:
            return

        self.running = True
        self._task = asyncio.create_task(self._poll_loop(on_scene_change), name="obs_scene_watcher")

    async def stop(self) -> None:
        """Stop scene polling task."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _poll_loop(self, on_scene_change: Callable[[str], Awaitable[None]]) -> None:
        """Background polling loop for current OBS scene."""
        while self.running:
            try:
                self.last_attempt_ts = time.time()
                scene = await asyncio.get_event_loop().run_in_executor(None, self._fetch_current_scene)
                if scene:
                    self.connected = True
                    self.connection_error = None
                    self.reconnect_attempts = 0
                    self.retry_backoff_sec = 1.0
                    self.last_connected_ts = time.time()

                    if scene != self._last_scene:
                        self._last_scene = scene
                        logger.info(f"OBS scene changed: {scene}")
                        await on_scene_change(scene)
                await asyncio.sleep(self.poll_interval)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.connected = False
                self.connection_error = str(exc)
                self.reconnect_attempts += 1
                self.next_retry_at = time.time() + self.retry_backoff_sec
                logger.debug(f"OBS scene watcher poll error: {exc}")
                await asyncio.sleep(self.retry_backoff_sec)
                self.retry_backoff_sec = min(self.retry_backoff_sec * 2.0, self.max_retry_backoff_sec)

    def reset_retry_backoff(self) -> None:
        """Reset retry state for a manual reconnect attempt."""
        self.retry_backoff_sec = 1.0
        self.next_retry_at = 0.0
        self.connection_error = None

    def get_diagnostics(self) -> dict:
        """Return watcher diagnostics for dock status/observability."""
        return {
            "connected": self.connected,
            "connection_error": self.connection_error,
            "last_attempt_ts": self.last_attempt_ts,
            "last_connected_ts": self.last_connected_ts,
            "next_retry_at": self.next_retry_at,
            "retry_backoff_sec": self.retry_backoff_sec,
            "reconnect_attempts": self.reconnect_attempts,
            "poll_interval": self.poll_interval,
        }

    def _fetch_current_scene(self) -> Optional[str]:
        """Blocking call to get current OBS program scene."""
        try:
            obs = importlib.import_module("obsws_python")
        except Exception as exc:
            raise RuntimeError("obsws-python is not installed") from exc

        client = None
        try:
            client = obs.ReqClient(host=self.host, port=self.port, password=self.password, timeout=2)
            response = client.get_current_program_scene()
            return getattr(response, "current_program_scene_name", None)
        finally:
            try:
                if client:
                    client.base_client.ws.close()
            except Exception:
                pass
