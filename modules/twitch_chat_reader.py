"""
Twitch chat reader module - Native Twitch IRC ingestion

Connects to Twitch IRC directly and ingests chat messages without OCR.
Supports both sync (threading) and async (event-driven) modes.
"""

import asyncio
import logging
import random
import re
import socket
import ssl
import threading
import time
import hashlib
from collections import deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

from config.config import AppConfig, get_config

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Represents a parsed chat message"""
    username: str
    message: str
    timestamp: float
    hash: str


class TwitchChatReader:
    """
    Reads Twitch chat directly from Twitch IRC.

    Features:
    - Native chat ingestion (no OCR)
    - Automatic reconnect
    - Message deduplication via hashing
    - First-time chatter detection per session
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self.running = False
        self.thread: Optional[threading.Thread] = None

        self.messages: deque = deque(maxlen=200)
        self.message_hashes: Set[str] = set()
        self.known_users: Set[str] = set()
        self.new_users: List[str] = []
        self.lock = threading.Lock()

        self.total_captures = 0
        self.successful_reads = 0
        self.last_capture_time = 0.0
        self.reconnects = 0
        self.auth_mode = "bot" if self.config.twitch_auth_prefer_bot else "anonymous"

        self._sock: Optional[socket.socket] = None
        self._buffer = ""
        self._emit_events = False  # Set to True when using async mode

        self._validate_config()

        logger.info(
            f"TwitchChatReader initialized for channel: {self.config.twitch_channel}"
        )

    def _validate_config(self) -> None:
        if not self.config.twitch_channel:
            raise RuntimeError("twitch_channel is required for Twitch chat ingestion")

    def _normalize_channel(self) -> str:
        channel = self.config.twitch_channel.strip().lower()
        if channel.startswith("#"):
            channel = channel[1:]
        return channel

    def _build_identity(self) -> tuple[str, str]:
        """
        Build PASS/NICK identity for Twitch IRC.

        Uses authenticated account if provided, otherwise anonymous justinfan login.
        """
        token = (self.config.twitch_oauth_token or "").strip()
        username = (self.config.twitch_bot_username or "").strip().lower()

        prefer_bot = self.config.twitch_auth_prefer_bot and self.auth_mode != "anonymous"

        if prefer_bot and token and username:
            if not token.startswith("oauth:"):
                token = f"oauth:{token}"
            self.auth_mode = "bot"
            return token, username

        anon_id = random.randint(10000, 99999)
        self.auth_mode = "anonymous"
        return "SCHMOOPIIE", f"justinfan{anon_id}"

    def _connect(self) -> None:
        host = "irc.chat.twitch.tv"
        port = 6697
        channel = self._normalize_channel()
        password, nickname = self._build_identity()

        raw_sock = socket.create_connection((host, port), timeout=10)
        self._sock = ssl.create_default_context().wrap_socket(raw_sock, server_hostname=host)
        self._sock.settimeout(1.0)

        self._send_line(f"PASS {password}")
        self._send_line(f"NICK {nickname}")
        self._send_line(f"JOIN #{channel}")

        logger.info(f"Connected to Twitch IRC as {nickname}, joined #{channel} ({self.auth_mode})")

    def _disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            finally:
                self._sock = None

    def _send_line(self, line: str) -> None:
        if not self._sock:
            return
        data = (line + "\r\n").encode("utf-8")
        self._sock.sendall(data)

    def _parse_privmsg(self, line: str) -> Optional[ChatMessage]:
        # Format: :username!username@username.tmi.twitch.tv PRIVMSG #channel :message
        match = re.match(r"^:([^!]+)!.* PRIVMSG #[^ ]+ :(.+)$", line)
        if not match:
            return None

        username, message = match.groups()
        message = message.strip()
        if not username or not message:
            return None

        msg_hash = hashlib.md5(f"{username}:{message}".encode("utf-8")).hexdigest()
        return ChatMessage(
            username=username,
            message=message,
            timestamp=time.time(),
            hash=msg_hash
        )

    def _process_message(self, msg: ChatMessage) -> None:
        with self.lock:
            if msg.hash in self.message_hashes:
                return

            self.messages.append(msg)
            self.message_hashes.add(msg.hash)

            if msg.username not in self.known_users:
                self.known_users.add(msg.username)
                self.new_users.append(msg.username)
                logger.info(f"First-time chatter: {msg.username}")

            self.successful_reads += 1
            self.total_captures += 1
            self.last_capture_time = time.time()

    def _poll_loop(self) -> None:
        logger.info("Twitch chat polling loop started")

        while self.running:
            try:
                if not self._sock:
                    self._connect()
                    self.reconnects += 1

                sock = self._sock
                if not sock:
                    raise ConnectionError("Twitch IRC socket not connected")

                data = sock.recv(4096)
                if not data:
                    raise ConnectionError("Twitch IRC connection closed")

                self._buffer += data.decode("utf-8", errors="ignore")

                while "\r\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\r\n", 1)
                    if not line:
                        continue

                    if line.startswith("PING "):
                        self._send_line(line.replace("PING", "PONG", 1))
                        continue

                    if "Login authentication failed" in line or "Improperly formatted auth" in line:
                        raise PermissionError("Twitch IRC authentication failed")

                    msg = self._parse_privmsg(line)
                    if msg:
                        self._process_message(msg)

            except socket.timeout:
                continue
            except PermissionError as e:
                if self.auth_mode == "bot" and self.config.twitch_anonymous_fallback:
                    logger.warning("Bot auth failed; switching to anonymous Twitch IRC mode")
                    self.auth_mode = "anonymous"
                    self._disconnect()
                    if self.running:
                        time.sleep(1.0)
                else:
                    logger.error(f"Twitch IRC authentication failed: {e}")
                    raise
            except Exception as e:
                logger.warning(f"Twitch IRC read error: {e}. Reconnecting in 3s...")
                self._disconnect()
                if self.running:
                    time.sleep(3.0)

    def start(self) -> None:
        if self.running:
            logger.warning("Twitch chat reader already running")
            return

        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logger.info("Twitch chat reader started")

    def stop(self) -> None:
        if not self.running:
            return

        logger.info("Stopping Twitch chat reader...")
        self.running = False

        if self.thread:
            self.thread.join(timeout=5.0)

        self._disconnect()
        logger.info("Twitch chat reader stopped")

    def get_recent_messages(self, count: int = 10) -> List[ChatMessage]:
        with self.lock:
            messages = list(self.messages)
            return messages[-count:] if len(messages) >= count else messages

    def get_new_users(self) -> List[str]:
        with self.lock:
            users = self.new_users.copy()
            self.new_users.clear()
            return users

    def get_message_count(self, seconds: float = 30.0) -> int:
        with self.lock:
            cutoff = time.time() - seconds
            return sum(1 for msg in self.messages if msg.timestamp >= cutoff)

    def get_stats(self) -> Dict[str, float]:
        with self.lock:
            return {
                "total_captures": self.total_captures,
                "successful_reads": self.successful_reads,
                "total_messages": len(self.messages),
                "unique_users": len(self.known_users),
                "last_capture": self.last_capture_time,
                "success_rate": (self.successful_reads / self.total_captures * 100)
                if self.total_captures > 0
                else 0.0,
                "reconnects": self.reconnects,
            }

    def reset_session(self) -> None:
        with self.lock:
            self.messages.clear()
            self.message_hashes.clear()
            self.known_users.clear()
            self.new_users.clear()
            logger.info("Twitch chat reader session reset")

    # ========================================================================
    # ASYNC EVENT-DRIVEN MODE (Phase 2a)
    # ========================================================================

    async def start_async(self, emit_events: bool = True) -> None:
        """
        Start async chat reader with event emission.
        
        Args:
            emit_events: If True, emit CHAT_MESSAGE events to event bus
        """
        if self.running:
            logger.warning("Twitch chat reader already running")
            return

        self.running = True
        self._emit_events = emit_events
        
        # Run async polling loop
        asyncio.create_task(self._poll_loop_async())
        logger.info("Twitch chat reader started (async mode)")

    async def _poll_loop_async(self) -> None:
        """Async polling loop with event emission."""
        logger.info("Twitch chat polling loop started (async)")

        while self.running:
            try:
                if not self._sock:
                    await self._connect_async()
                    self.reconnects += 1

                sock = self._sock
                if not sock:
                    raise ConnectionError("Twitch IRC socket not connected")

                # Non-blocking read with timeout
                await asyncio.sleep(0.01)  # Small yield to event loop
                sock.settimeout(0.1)  # Short timeout for non-blocking behavior
                
                try:
                    data = sock.recv(4096)
                except socket.timeout:
                    continue
                
                if not data:
                    raise ConnectionError("Twitch IRC connection closed")

                self._buffer += data.decode("utf-8", errors="ignore")

                while "\r\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\r\n", 1)
                    if not line:
                        continue

                    if line.startswith("PING "):
                        self._send_line(line.replace("PING", "PONG", 1))
                        continue

                    if "Login authentication failed" in line or "Improperly formatted auth" in line:
                        raise PermissionError("Twitch IRC authentication failed")

                    msg = self._parse_privmsg(line)
                    if msg:
                        self._process_message(msg)
                        
                        # Emit event if in async mode
                        if self._emit_events:
                            await self._emit_chat_event(msg)

            except PermissionError as e:
                if self.auth_mode == "bot" and self.config.twitch_anonymous_fallback:
                    logger.warning("Bot auth failed; switching to anonymous Twitch IRC mode")
                    self.auth_mode = "anonymous"
                    self._disconnect()
                    if self.running:
                        await asyncio.sleep(1.0)
                else:
                    logger.error(f"Twitch IRC authentication failed: {e}")
                    raise
            except Exception as e:
                logger.warning(f"Twitch IRC read error: {e}. Reconnecting in 3s...")
                self._disconnect()
                if self.running:
                    await asyncio.sleep(3.0)

    async def _connect_async(self) -> None:
        """Async connection to Twitch IRC (uses sync socket for compatibility)."""
        # NOTE: socket.create_connection is blocking, but connection is fast (<1s)
        # For true async, would need asyncio.open_connection, but keeping simple for now
        await asyncio.get_event_loop().run_in_executor(None, self._connect)

    async def _emit_chat_event(self, msg: ChatMessage) -> None:
        """Emit CHAT_MESSAGE event to event bus."""
        try:
            from core.event_bus import Event, EventType, EventPriority, get_event_bus
            
            event = Event(
                type=EventType.CHAT_MESSAGE,
                priority=EventPriority.NORMAL,
                data={
                    "username": msg.username,
                    "message": msg.message,
                    "timestamp": msg.timestamp,
                    "hash": msg.hash,
                },
                source="twitch_chat_reader"
            )
            
            bus = get_event_bus()
            await bus.publish(event)
            
        except Exception as e:
            logger.error(f"Failed to emit chat event: {e}")

    async def stop_async(self) -> None:
        """Stop async chat reader."""
        if not self.running:
            return

        logger.info("Stopping Twitch chat reader (async)...")
        self.running = False
        
        # Give time for final events to emit
        await asyncio.sleep(0.5)
        
        self._disconnect()
        logger.info("Twitch chat reader stopped (async)")
