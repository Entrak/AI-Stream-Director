"""
Twitch stream stats module - Helix API stream metrics ingestion.

Uses Twitch API tokens to fetch live stream metrics for the configured channel.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional, Dict, Any

from config.config import AppConfig, get_config
from modules.twitch_oauth import TwitchOAuthManager

logger = logging.getLogger(__name__)


class TwitchStreamStats:
    """Polls Twitch Helix streams endpoint and stores latest stream stats."""

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()

        self.client_id = (os.getenv("TWITCH_CLIENT_ID") or "").strip()
        self.client_secret = (os.getenv("TWITCH_CLIENT_SECRET") or "").strip()

        self._token: Optional[str] = None
        self._token_expires_at = 0.0
        self.oauth = TwitchOAuthManager(self.config)

        self.running = False
        self.thread: Optional[threading.Thread] = None

        self._lock = threading.Lock()
        self._latest_stats: Dict[str, Any] = {
            "is_live": False,
            "viewer_count": 0,
            "game_name": "",
            "title": "",
            "started_at": "",
            "last_updated": 0.0,
            "error": None,
        }

        logger.info("TwitchStreamStats initialized")

    def is_configured(self) -> bool:
        channel = (self.config.twitch_channel or "").strip().lower()
        if channel.startswith("#"):
            channel = channel[1:]
        if channel in ["", "your_twitch_channel"]:
            return False
        return bool(channel and self.client_id and self.client_secret)

    def _normalize_channel(self) -> str:
        channel = (self.config.twitch_channel or "").strip().lower()
        return channel[1:] if channel.startswith("#") else channel

    def _request_json(self, url: str, headers: Dict[str, str], timeout: float = 10.0) -> Dict[str, Any]:
        req = urllib.request.Request(url=url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = response.read().decode("utf-8", errors="ignore")
            return json.loads(payload)

    def _get_app_access_token(self) -> str:
        now = time.time()
        if self._token and now < self._token_expires_at - 30:
            return self._token

        params = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "client_credentials",
            }
        )
        token_url = f"https://id.twitch.tv/oauth2/token?{params}"
        req = urllib.request.Request(url=token_url, method="POST")

        with urllib.request.urlopen(req, timeout=10.0) as response:
            data = json.loads(response.read().decode("utf-8", errors="ignore"))

        access_token = data.get("access_token")
        expires_in = int(data.get("expires_in", 0))
        if not access_token or expires_in <= 0:
            raise RuntimeError("Invalid Twitch token response")

        self._token = access_token
        self._token_expires_at = now + expires_in
        return access_token

    def _fetch_stream_stats(self) -> Dict[str, Any]:
        if self.config.twitch_require_user_auth:
            token = self.oauth.get_access_token(interactive=True)
            if not token:
                raise RuntimeError("Twitch user authorization required. Please authorize in browser when prompted.")
        else:
            token = self._get_app_access_token()

        channel = self._normalize_channel()
        url = f"https://api.twitch.tv/helix/streams?user_login={urllib.parse.quote(channel)}"

        headers = {
            "Client-Id": self.client_id,
            "Authorization": f"Bearer {token}",
        }

        payload = self._request_json(url, headers=headers, timeout=10.0)
        data = payload.get("data", [])
        if not data:
            return {
                "is_live": False,
                "viewer_count": 0,
                "game_name": "",
                "title": "",
                "started_at": "",
                "last_updated": time.time(),
                "error": None,
            }

        stream = data[0]
        return {
            "is_live": True,
            "viewer_count": int(stream.get("viewer_count", 0) or 0),
            "game_name": stream.get("game_name", "") or "",
            "title": stream.get("title", "") or "",
            "started_at": stream.get("started_at", "") or "",
            "last_updated": time.time(),
            "error": None,
        }

    def _poll_loop(self) -> None:
        logger.info("Twitch stream stats polling loop started")

        while self.running:
            try:
                stats = self._fetch_stream_stats()
                with self._lock:
                    self._latest_stats = stats
                logger.debug(
                    f"Twitch stats: live={stats['is_live']} viewers={stats['viewer_count']} game={stats['game_name']}"
                )
            except urllib.error.HTTPError as e:
                error_body = ""
                try:
                    error_body = e.read().decode("utf-8", errors="ignore").strip()
                except Exception:
                    error_body = ""

                message = f"Twitch API HTTP error: {e.code}"
                if error_body:
                    message = f"{message} - {error_body}"
                logger.warning(message)
                with self._lock:
                    self._latest_stats["error"] = message
                    self._latest_stats["last_updated"] = time.time()
            except Exception as e:
                message = f"Twitch API poll failed: {e}"
                logger.warning(message)
                with self._lock:
                    self._latest_stats["error"] = message
                    self._latest_stats["last_updated"] = time.time()

            time.sleep(self.config.twitch_stats_poll_interval)

    def probe_once(self, interactive_auth: bool = True) -> Dict[str, Any]:
        if not self.is_configured():
            return {
                "ok": False,
                "error": "Twitch stats not configured",
            }

        try:
            if self.config.twitch_require_user_auth:
                token = self.oauth.get_access_token(interactive=interactive_auth)
                if not token:
                    return {
                        "ok": False,
                        "error": "Twitch user authorization not completed",
                    }

            stats = self._fetch_stream_stats()
            with self._lock:
                self._latest_stats = stats
            return {
                "ok": True,
                "stats": stats,
            }
        except Exception as e:
            message = f"Twitch stats probe failed: {e}"
            with self._lock:
                self._latest_stats["error"] = message
                self._latest_stats["last_updated"] = time.time()
            return {
                "ok": False,
                "error": message,
            }

    def start(self) -> None:
        if not self.config.twitch_stats_enabled:
            logger.info("Twitch stream stats disabled by config")
            return

        if not self.is_configured():
            logger.info("Twitch stream stats not configured (set TWITCH_CLIENT_ID, TWITCH_CLIENT_SECRET, and a valid twitch_channel)")
            return

        if self.config.twitch_require_user_auth:
            token = self.oauth.get_access_token(interactive=True)
            if not token:
                logger.warning("Twitch stream stats disabled: user authorization not completed")
                return

        if self.running:
            return

        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logger.info("Twitch stream stats started")

    def stop(self) -> None:
        if not self.running:
            return

        self.running = False
        if self.thread:
            self.thread.join(timeout=5.0)
        logger.info("Twitch stream stats stopped")

    def get_latest_stats(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._latest_stats)
