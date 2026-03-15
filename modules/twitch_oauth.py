"""
Twitch OAuth helper - interactive user authorization + token refresh.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional, Tuple
from uuid import uuid4

from config.config import AppConfig, get_config_manager

logger = logging.getLogger(__name__)


class TwitchOAuthManager:
    """Handles Twitch user OAuth authorization code flow for local desktop usage."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.client_id = (os.getenv("TWITCH_CLIENT_ID") or "").strip()
        self.client_secret = (os.getenv("TWITCH_CLIENT_SECRET") or "").strip()
        self._lock = threading.Lock()

    def is_configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    def has_valid_token(self) -> bool:
        token = (self.config.twitch_user_access_token or "").strip()
        expires_at = float(self.config.twitch_user_token_expires_at or 0.0)
        return bool(token and time.time() < expires_at - 30)

    def get_access_token(self, interactive: bool = True) -> Optional[str]:
        with self._lock:
            if self.has_valid_token():
                return self.config.twitch_user_access_token

            if self._refresh_access_token():
                return self.config.twitch_user_access_token

            if interactive and self.config.twitch_require_user_auth:
                if self._interactive_authorize():
                    return self.config.twitch_user_access_token

            return None

    def _persist_tokens(self, access_token: str, refresh_token: str, expires_in: int) -> None:
        self.config.twitch_user_access_token = access_token
        self.config.twitch_user_refresh_token = refresh_token
        self.config.twitch_user_token_expires_at = time.time() + max(int(expires_in), 60)
        get_config_manager().save()

    def _refresh_access_token(self) -> bool:
        refresh_token = (self.config.twitch_user_refresh_token or "").strip()
        if not refresh_token or not self.is_configured():
            return False

        try:
            payload = urllib.parse.urlencode(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                url="https://id.twitch.tv/oauth2/token",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            with urllib.request.urlopen(req, timeout=15.0) as response:
                data = json.loads(response.read().decode("utf-8", errors="ignore"))

            access_token = data.get("access_token", "")
            new_refresh_token = data.get("refresh_token", refresh_token)
            expires_in = int(data.get("expires_in", 0) or 0)
            if not access_token or expires_in <= 0:
                return False

            self._persist_tokens(access_token, new_refresh_token, expires_in)
            logger.info("Refreshed Twitch user access token")
            return True
        except Exception as e:
            logger.warning(f"Twitch token refresh failed: {e}")
            return False

    def _interactive_authorize(self) -> bool:
        if not self.is_configured():
            logger.warning("Twitch OAuth not configured. Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET")
            return False

        try:
            redirect = urllib.parse.urlparse(self.config.twitch_redirect_uri)
            host = redirect.hostname or "localhost"
            port = int(redirect.port or 8085)
            path = redirect.path or "/callback"
        except Exception as e:
            logger.error(f"Invalid twitch_redirect_uri '{self.config.twitch_redirect_uri}': {e}")
            return False

        state = uuid4().hex
        code_result: dict[str, str] = {}
        done = threading.Event()

        class CallbackHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != path:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"Not found")
                    return

                query = urllib.parse.parse_qs(parsed.query)
                returned_state = (query.get("state") or [""])[0]
                if returned_state != state:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Invalid OAuth state")
                    code_result["error"] = "Invalid OAuth state"
                    done.set()
                    return

                error = (query.get("error") or [""])[0]
                code = (query.get("code") or [""])[0]

                if error:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"Twitch authorization was denied. You can close this window.")
                    code_result["error"] = error
                    done.set()
                    return

                if not code:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Missing code")
                    code_result["error"] = "Missing authorization code"
                    done.set()
                    return

                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Twitch authorization successful. You can close this window and return to the app.")
                code_result["code"] = code
                done.set()

            def log_message(self, format, *args):
                return

        try:
            server = HTTPServer((host, port), CallbackHandler)
        except Exception as e:
            logger.error(f"Failed to start local Twitch OAuth callback server at {host}:{port}: {e}")
            return False

        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        auth_params = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self.config.twitch_redirect_uri,
                "scope": "",
                "state": state,
            }
        )
        auth_url = f"https://id.twitch.tv/oauth2/authorize?{auth_params}"

        logger.info("Opening browser for Twitch authorization...")
        logger.info(f"If browser does not open, navigate to: {auth_url}")
        webbrowser.open(auth_url)

        completed = done.wait(timeout=180.0)
        server.shutdown()
        server.server_close()

        if not completed:
            logger.warning("Timed out waiting for Twitch authorization callback")
            return False

        code = code_result.get("code", "")
        if not code:
            logger.warning(f"Twitch authorization not completed: {code_result.get('error', 'unknown error')}")
            return False

        return self._exchange_code_for_tokens(code)

    def _exchange_code_for_tokens(self, code: str) -> bool:
        try:
            payload = urllib.parse.urlencode(
                {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": self.config.twitch_redirect_uri,
                }
            ).encode("utf-8")

            req = urllib.request.Request(
                url="https://id.twitch.tv/oauth2/token",
                data=payload,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            with urllib.request.urlopen(req, timeout=15.0) as response:
                data = json.loads(response.read().decode("utf-8", errors="ignore"))

            access_token = data.get("access_token", "")
            refresh_token = data.get("refresh_token", "")
            expires_in = int(data.get("expires_in", 0) or 0)

            if not access_token or not refresh_token or expires_in <= 0:
                logger.error("Invalid Twitch token exchange response")
                return False

            self._persist_tokens(access_token, refresh_token, expires_in)
            logger.info("Twitch authorization completed successfully")
            return True
        except urllib.error.HTTPError as e:
            message = ""
            try:
                message = e.read().decode("utf-8", errors="ignore")
            except Exception:
                message = ""
            logger.error(f"Twitch token exchange failed: HTTP {e.code} {message}")
            return False
        except Exception as e:
            logger.error(f"Twitch token exchange failed: {e}")
            return False
