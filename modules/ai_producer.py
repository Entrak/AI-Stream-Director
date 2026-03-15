"""
AI Producer module - Ollama-powered stream feedback generation

Analyzes chat and voice data to generate actionable streaming tips.
"""

import logging
import time
from typing import Optional, Dict, List
import ollama

from config.config import get_config, AppConfig

logger = logging.getLogger(__name__)


class AIProducer:
    """
    AI-powered stream producer using Ollama
    
    Analyzes chat activity and voice metrics to generate:
    - Engagement suggestions
    - Pacing feedback
    - Content ideas
    - First-time chatter welcomes
    """
    
    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or get_config()
        
        # Ollama client
        self.client = ollama.Client(host=self.config.ollama_host)
        
        # Feedback tracking
        self.last_feedback_time = 0.0
        self.total_feedbacks = 0
        
        # Verify Ollama connection
        self._check_ollama()
        
        logger.info(f"AIProducer initialized with model: {self.config.ollama_model}")
    
    def _check_ollama(self) -> None:
        """Verify Ollama is accessible and model is available"""
        try:
            # List available models
            models = self.client.list()
            model_names = [m['name'] for m in models.get('models', [])]
            
            logger.info(f"Available Ollama models: {model_names}")
            
            # Check if configured model is available
            model_short = self.config.ollama_model.split(':')[0]
            if not any(model_short in name for name in model_names):
                logger.warning(
                    f"Model '{self.config.ollama_model}' not found in Ollama. "
                    f"Pull with: ollama pull {self.config.ollama_model}"
                )
        
        except Exception as e:
            logger.error(f"Cannot connect to Ollama: {e}")
            logger.error(f"Ensure Ollama is running and accessible at {self.config.ollama_host}")
            raise RuntimeError(f"Ollama connection failed: {e}")
    
    def should_trigger(self, chat_data: Dict, voice_data: Dict, new_users: List[str]) -> bool:
        """
        Determine if feedback should be generated based on triggers
        
        Args:
            chat_data: Chat metrics dict
            voice_data: Voice metrics dict
            new_users: List of new chatters
        
        Returns:
            True if feedback should be generated
        """
        # Check cooldown
        time_since_last = time.time() - self.last_feedback_time
        if time_since_last < self.config.feedback_cooldown:
            return False
        
        # Trigger: New first-time chatter
        if new_users:
            logger.info(f"Trigger: New chatter(s) - {', '.join(new_users)}")
            return True
        
        # Trigger: Chat is slow
        if chat_data.get('recent_message_count', 0) < self.config.chat_slow_threshold:
            logger.info(f"Trigger: Chat slow ({chat_data.get('recent_message_count', 0)} msgs)")
            return True
        
        # Trigger: Speaking too fast
        wpm = voice_data.get('words_per_minute', 0)
        if wpm > self.config.words_per_min_max:
            logger.info(f"Trigger: Speaking too fast ({wpm:.0f} WPM)")
            return True
        
        # Trigger: Speaking too slow
        if 0 < wpm < self.config.words_per_min_min:
            logger.info(f"Trigger: Speaking too slow ({wpm:.0f} WPM)")
            return True
        
        # Trigger: Too many fillers
        fillers = voice_data.get('filler_count', 0)
        if fillers > self.config.max_filler_count_per_min:
            logger.info(f"Trigger: Excessive fillers ({fillers})")
            return True
        
        # Trigger: Regular interval (fallback)
        if time_since_last >= 120.0:  # 2 minutes minimum
            logger.info("Trigger: Regular interval check")
            return True
        
        return False
    
    def _build_prompt(
        self,
        chat_data: Dict,
        voice_data: Dict,
        new_users: List[str],
        recent_messages: List
    ) -> str:
        """
        Build prompt for Ollama based on current context
        
        Args:
            chat_data: Chat metrics
            voice_data: Voice metrics
            new_users: New chatters
            recent_messages: Recent chat messages
        
        Returns:
            Formatted prompt string
        """
        # System context
        system_msg = (
            "You are an AI stream producer for a Twitch streamer. "
            "Give 1-2 actionable, encouraging tips in under 50 words. "
            "Be specific and concise. Focus on the most important issue."
        )
        
        # Build context sections
        context_parts = []
        
        # Chat analysis
        msg_count = chat_data.get('recent_message_count', 0)
        total_msgs = chat_data.get('total_messages', 0)
        
        chat_context = f"Chat activity: {msg_count} messages in last 30s, {total_msgs} total."
        
        if new_users:
            chat_context += f"\nNew chatters: {', '.join(new_users[:3])}"
        
        if recent_messages:
            chat_samples = "\n".join([
                f"  - {msg.username}: {msg.message[:60]}"
                for msg in recent_messages[-3:]
            ])
            chat_context += f"\nRecent messages:\n{chat_samples}"
        
        context_parts.append(chat_context)
        
        # Voice analysis
        wpm = voice_data.get('words_per_minute', 0)
        fillers = voice_data.get('filler_count', 0)
        energy = voice_data.get('energy_level', 0)
        
        if wpm > 0:
            voice_context = (
                f"Voice metrics: {wpm:.0f} words/min, "
                f"{fillers} filler words, "
                f"energy level {energy:.1f}/1.0"
            )
            context_parts.append(voice_context)
        
        # Identify key issues
        issues = []
        
        if new_users:
            issues.append(f"Welcome new chatters: {', '.join(new_users[:2])}")
        
        if msg_count < self.config.chat_slow_threshold:
            issues.append("Chat is quiet - consider engaging viewers")
        
        if wpm > self.config.words_per_min_max:
            issues.append(f"Speaking too fast ({wpm:.0f} WPM)")
        elif 0 < wpm < self.config.words_per_min_min:
            issues.append(f"Speaking too slow ({wpm:.0f} WPM)")
        
        if fillers > self.config.max_filler_count_per_min:
            issues.append(f"Too many filler words ({fillers} in last minute)")
        
        # Build final prompt
        prompt_parts = [
            f"CONTEXT:",
            "\n".join(context_parts),
            "",
            "KEY ISSUES:" if issues else "GENERAL CHECK:",
            "\n".join(f"- {issue}" for issue in issues) if issues else "- Provide general stream improvement tip",
            "",
            "Provide 1-2 actionable tips (under 50 words):"
        ]
        
        prompt = "\n".join(prompt_parts)
        
        logger.debug(f"Generated prompt:\n{prompt}")
        
        return prompt
    
    def generate_feedback(
        self,
        chat_data: Dict,
        voice_data: Dict,
        new_users: List[str],
        recent_messages: List = None
    ) -> Optional[str]:
        """
        Generate AI feedback based on current stream state
        
        Args:
            chat_data: Chat metrics dict
            voice_data: Voice metrics dict
            new_users: List of new chatter usernames
            recent_messages: Optional list of recent ChatMessage objects
        
        Returns:
            Feedback text or None if generation fails
        """
        recent_messages = recent_messages or []
        
        try:
            # Build prompt
            prompt = self._build_prompt(chat_data, voice_data, new_users, recent_messages)
            
            # Call Ollama
            start_time = time.time()
            
            response = self.client.generate(
                model=self.config.ollama_model,
                prompt=prompt,
                options={
                    'temperature': 0.7,
                    'top_p': 0.9,
                    'max_tokens': 100,  # Enforce brevity
                }
            )
            
            elapsed = time.time() - start_time
            
            # Extract response
            feedback = response.get('response', '').strip()
            
            # Trim to word limit
            words = feedback.split()
            if len(words) > self.config.max_feedback_words:
                feedback = ' '.join(words[:self.config.max_feedback_words]) + "..."
            
            logger.info(f"Generated feedback in {elapsed:.2f}s: {feedback[:80]}...")
            
            # Update tracking
            self.last_feedback_time = time.time()
            self.total_feedbacks += 1
            
            return feedback
        
        except Exception as e:
            logger.error(f"Failed to generate feedback: {e}", exc_info=True)
            return None
    
    def get_stats(self) -> Dict:
        """Get producer statistics"""
        return {
            "total_feedbacks": self.total_feedbacks,
            "last_feedback_time": self.last_feedback_time,
            "time_since_last": time.time() - self.last_feedback_time
        }


# Example prompt templates for different scenarios
PROMPT_TEMPLATES = {
    "new_chatter": """
You are a Twitch stream producer. A new viewer just chatted for the first time.

New chatter: {username}
Recent chat: {recent_chat}

Give a warm, specific welcome suggestion in under 30 words.
""",
    
    "slow_chat": """
You are a Twitch stream producer. Chat activity is low.

Messages in last 30s: {message_count}
Current activity: {activity_description}

Suggest one engaging question or topic to spark conversation (under 40 words).
""",
    
    "pacing_fast": """
You are a Twitch stream producer. The streamer is speaking very fast.

Current pace: {wpm} words per minute (target: 100-220)

Give a brief tip to slow down and improve clarity (under 30 words).
""",
    
    "pacing_slow": """
You are a Twitch stream producer. The streamer is speaking slowly with low energy.

Current pace: {wpm} words per minute
Energy level: {energy}

Suggest how to increase energy and engagement (under 35 words).
""",
    
    "excessive_fillers": """
You are a Twitch stream producer. The streamer is using too many filler words.

Filler words in last minute: {filler_count} (um, uh, like, you know)

Give a quick tip to reduce fillers WITHOUT being discouraging (under 30 words).
""",
}


if __name__ == "__main__":
    # Test AI producer
    import sys
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    producer = AIProducer()
    
    print("Testing AI Producer...\n")
    
    # Test scenario: New chatter + slow chat
    test_chat_data = {
        "recent_message_count": 2,
        "total_messages": 15
    }
    
    test_voice_data = {
        "words_per_minute": 180.0,
        "filler_count": 3,
        "energy_level": 0.6
    }
    
    test_new_users = ["TestUser123"]
    
    print("Test 1: New chatter welcome")
    print("-" * 50)
    
    if producer.should_trigger(test_chat_data, test_voice_data, test_new_users):
        feedback = producer.generate_feedback(
            test_chat_data,
            test_voice_data,
            test_new_users
        )
        print(f"Feedback: {feedback}\n")
    
    # Test scenario: Speaking too fast
    test_voice_data2 = {
        "words_per_minute": 250.0,
        "filler_count": 5,
        "energy_level": 0.8
    }
    
    producer.last_feedback_time = 0  # Reset cooldown
    
    print("\nTest 2: Speaking too fast")
    print("-" * 50)
    
    if producer.should_trigger(test_chat_data, test_voice_data2, []):
        feedback = producer.generate_feedback(
            test_chat_data,
            test_voice_data2,
            []
        )
        print(f"Feedback: {feedback}\n")
    
    stats = producer.get_stats()
    print(f"\nStats: {stats}")
