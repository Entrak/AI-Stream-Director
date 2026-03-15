"""
Chat reader module - OCR-based Twitch chat extraction

Captures screenshots of OBS chat region and extracts messages using OCR.
"""

import logging
import re
import sys
import time
import hashlib
import threading
from collections import deque
from dataclasses import dataclass
from typing import List, Set, Optional, Dict
import cv2
import numpy as np
import pytesseract
from mss import mss

from config.config import get_config, AppConfig

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Represents a parsed chat message"""
    username: str
    message: str
    timestamp: float
    hash: str


class ChatReader:
    """
    Reads Twitch chat via OCR on OBS window screenshots
    
    Features:
    - Periodic screenshot capture of configured region
    - Image preprocessing for better OCR accuracy
    - Message parsing with regex
    - Deduplication via message hashing
    - First-time chatter detection
    """
    
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        
        # Message history (thread-safe with lock)
        self.messages: deque = deque(maxlen=50)
        self.message_hashes: Set[str] = set()
        self.known_users: Set[str] = set()
        self.new_users: List[str] = []
        self.lock = threading.Lock()
        
        # Performance metrics
        self.total_captures = 0
        self.successful_reads = 0
        self.last_capture_time = 0.0
        
        # Message parsing regex
        # Matches: "username: message" or "username : message"
        self.username_pattern = re.compile(r'^([\w\d_]{3,25})\s*:\s*(.+)$', re.IGNORECASE)
        
        # Initialize pytesseract
        self._check_tesseract()
        
        logger.info(f"ChatReader initialized with poll interval: {self.config.chat_poll_interval}s")
    
    def _check_tesseract(self):
        """Verify Tesseract is installed and accessible"""
        try:
            version = pytesseract.get_tesseract_version()
            logger.info(f"Tesseract version: {version}")
        except Exception as e:
            logger.error(f"Tesseract not found: {e}")
            raise RuntimeError(
                "Tesseract OCR not installed. "
                "Install from: https://github.com/UB-Mannheim/tesseract/wiki"
            )
    
    def _capture_region(self) -> Optional[np.ndarray]:
        """
        Capture screenshot of configured chat region
        
        Returns:
            Numpy array (BGR format) or None if capture fails
        """
        if not self.config.chat_region:
            logger.warning("Chat region not configured")
            return None
        
        try:
            with mss() as sct:
                region = self.config.chat_region
                monitor = {
                    "top": region.y,
                    "left": region.x,
                    "width": region.width,
                    "height": region.height
                }
                
                screenshot = sct.grab(monitor)
                img = np.array(screenshot)
                img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                
                self.total_captures += 1
                self.last_capture_time = time.time()
                
                return img
        
        except Exception as e:
            logger.error(f"Failed to capture screenshot: {e}")
            return None
    
    def _preprocess_image(self, img: np.ndarray) -> np.ndarray:
        """
        Preprocess image for better OCR accuracy
        
        Optimized for Twitch chat (white text on dark background)
        
        Args:
            img: Input image (BGR format)
        
        Returns:
            Preprocessed grayscale image
        """
        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Increase contrast
        gray = cv2.convertScaleAbs(gray, alpha=1.5, beta=10)
        
        # Apply adaptive thresholding (works better than global threshold for varying backgrounds)
        processed = cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            11,
            2
        )
        
        # Optional: denoise for better accuracy (can slow down OCR)
        # processed = cv2.fastNlMeansDenoising(processed)
        
        return processed
    
    def _extract_text(self, img: np.ndarray) -> str:
        """
        Extract text from image using OCR
        
        Args:
            img: Preprocessed image
        
        Returns:
            Extracted text string
        """
        try:
            # Use pytesseract with custom config
            # --psm 6: Assume uniform block of text
            # --oem 3: Use LSTM neural net mode
            text = pytesseract.image_to_string(
                img,
                config=self.config.tesseract_config
            )
            
            return text.strip()
        
        except Exception as e:
            logger.error(f"OCR extraction failed: {e}")
            return ""
    
    def _parse_messages(self, text: str) -> List[ChatMessage]:
        """
        Parse OCR text into chat messages
        
        Args:
            text: Raw OCR text
        
        Returns:
            List of parsed ChatMessage objects
        """
        messages = []
        lines = text.split('\n')
        
        for line in lines:
            line = line.strip()
            if not line:
                continue
            
            # Try to match username: message pattern
            match = self.username_pattern.match(line)
            if match:
                username, message = match.groups()
                
                # Create hash for deduplication
                msg_hash = hashlib.md5(f"{username}:{message}".encode()).hexdigest()
                
                messages.append(ChatMessage(
                    username=username,
                    message=message,
                    timestamp=time.time(),
                    hash=msg_hash
                ))
        
        return messages
    
    def _process_messages(self, messages: List[ChatMessage]) -> None:
        """
        Process parsed messages: deduplicate, track users, update history
        
        Args:
            messages: List of ChatMessage objects
        """
        with self.lock:
            new_messages = []
            
            for msg in messages:
                # Skip duplicates
                if msg.hash in self.message_hashes:
                    continue
                
                # Add to history
                self.messages.append(msg)
                self.message_hashes.add(msg.hash)
                new_messages.append(msg)
                
                # Track first-time chatters
                if msg.username not in self.known_users:
                    self.known_users.add(msg.username)
                    self.new_users.append(msg.username)
                    logger.info(f"First-time chatter: {msg.username}")
            
            if new_messages:
                self.successful_reads += 1
                logger.debug(f"Processed {len(new_messages)} new messages")
                for msg in new_messages:
                    logger.debug(f"  {msg.username}: {msg.message[:50]}...")
    
    def _poll_loop(self) -> None:
        """Main polling loop (runs in separate thread)"""
        logger.info("Chat reader polling loop started")
        
        while self.running:
            try:
                # Capture screenshot
                img = self._capture_region()
                if img is None:
                    time.sleep(self.config.chat_poll_interval)
                    continue
                
                # Preprocess for OCR
                processed = self._preprocess_image(img)
                
                # Extract text
                text = self._extract_text(processed)
                
                if text:
                    # Parse messages
                    messages = self._parse_messages(text)
                    
                    # Process and store
                    self._process_messages(messages)
                
                # Wait before next poll
                time.sleep(self.config.chat_poll_interval)
            
            except Exception as e:
                logger.error(f"Error in chat reader poll loop: {e}", exc_info=True)
                time.sleep(self.config.chat_poll_interval)
    
    def start(self) -> None:
        """Start the chat reader polling loop"""
        if self.running:
            logger.warning("Chat reader already running")
            return
        
        if not self.config.is_calibrated():
            raise RuntimeError("Chat region not calibrated. Run with --calibrate flag first.")
        
        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.thread.start()
        logger.info("Chat reader started")
    
    def stop(self) -> None:
        """Stop the chat reader polling loop"""
        if not self.running:
            return
        
        logger.info("Stopping chat reader...")
        self.running = False
        if self.thread:
            self.thread.join(timeout=5.0)
        logger.info("Chat reader stopped")
    
    def get_recent_messages(self, count: int = 10) -> List[ChatMessage]:
        """
        Get most recent chat messages
        
        Args:
            count: Number of recent messages to return
        
        Returns:
            List of ChatMessage objects (newest first)
        """
        with self.lock:
            messages = list(self.messages)
            return messages[-count:] if len(messages) >= count else messages
    
    def get_new_users(self) -> List[str]:
        """
        Get list of new users since last check (and clear the list)
        
        Returns:
            List of usernames
        """
        with self.lock:
            users = self.new_users.copy()
            self.new_users.clear()
            return users
    
    def get_message_count(self, seconds: float = 30.0) -> int:
        """
        Get number of messages in the last N seconds
        
        Args:
            seconds: Time window in seconds
        
        Returns:
            Message count
        """
        with self.lock:
            cutoff = time.time() - seconds
            count = sum(1 for msg in self.messages if msg.timestamp >= cutoff)
            return count
    
    def get_stats(self) -> Dict[str, any]:
        """
        Get reader statistics
        
        Returns:
            Dict with stats
        """
        with self.lock:
            return {
                "total_captures": self.total_captures,
                "successful_reads": self.successful_reads,
                "total_messages": len(self.messages),
                "unique_users": len(self.known_users),
                "last_capture": self.last_capture_time,
                "success_rate": (self.successful_reads / self.total_captures * 100) 
                    if self.total_captures > 0 else 0.0
            }
    
    def reset_session(self) -> None:
        """Reset session state (users, messages) for new stream"""
        with self.lock:
            self.messages.clear()
            self.message_hashes.clear()
            self.known_users.clear()
            self.new_users.clear()
            logger.info("Chat reader session reset")


if __name__ == "__main__":
    # Test chat reader
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    from config.config import get_config_manager
    
    config_manager = get_config_manager()
    
    if not config_manager.config.is_calibrated():
        print("Chat region not calibrated. Run setup_wizard.py first.")
        sys.exit(1)
    
    reader = ChatReader()
    
    print(f"Starting chat reader test...")
    print(f"Region: {config_manager.config.chat_region}")
    print(f"Polling every {config_manager.config.chat_poll_interval}s")
    print("Press Ctrl+C to stop\n")
    
    reader.start()
    
    try:
        while True:
            time.sleep(10)
            
            # Print stats every 10 seconds
            stats = reader.get_stats()
            recent = reader.get_recent_messages(5)
            new_users = reader.get_new_users()
            
            print(f"\n--- Stats (10s update) ---")
            print(f"Captures: {stats['total_captures']}, Success: {stats['successful_reads']}")
            print(f"Total messages: {stats['total_messages']}, Unique users: {stats['unique_users']}")
            print(f"Success rate: {stats['success_rate']:.1f}%")
            
            if new_users:
                print(f"New chatters: {', '.join(new_users)}")
            
            if recent:
                print(f"\nRecent messages:")
                for msg in recent[-3:]:
                    print(f"  {msg.username}: {msg.message[:60]}")
    
    except KeyboardInterrupt:
        print("\n\nStopping...")
        reader.stop()
        print("Done!")
