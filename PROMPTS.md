# AI Producer Prompt Examples

This document contains example prompts and customization ideas for the AI feedback system.

## Overview

The AI Producer uses Ollama (Qwen3:8B) to generate feedback based on:
- Recent chat messages (last 5-10)
- Voice metrics (words/min, filler count, energy)
- Trigger conditions (new chatters, slow chat, pacing issues)

Prompts are constructed in [modules/ai_producer.py](modules/ai_producer.py) in the `_build_prompt()` method.

## Default Prompt Structure

```
CONTEXT:
Chat activity: 5 messages in last 30s, 42 total.
New chatters: UserA, UserB
Recent messages:
  - UserA: First time watching!
  - UserB: What game is this?
  - UserC: Love your content

Voice metrics: 180 words/min, 2 filler words, energy level 0.6/1.0

KEY ISSUES:
- Welcome new chatters: UserA, UserB

Provide 1-2 actionable tips (under 50 words):
```

## Example Prompts by Scenario

### 1. New Chatter Welcome

**Context:**
- New user just chatted for first time
- Want warm, specific welcome

**Prompt:**
```
You are a Twitch stream producer. A new viewer just chatted.

New chatter: CoolUser123
Their message: "First time here, looks fun!"
Recent chat context: 
  - OldViewer: "Welcome!"
  - StreamerBot: "Thanks for the follow!"

Give a warm welcome suggestion that:
- Uses their username
- References their message
- Encourages engagement
- Under 30 words

Example: "Say: Welcome CoolUser123! Glad you're enjoying it - what brought you to the stream today?"
```

**Expected Output:**
> "Welcome CoolUser123! Great to have you here for the first time. Ask them what brought them to the stream!"

---

### 2. Slow Chat / Low Engagement

**Context:**
- Less than 3 messages in last 30 seconds
- Need to spark conversation

**Prompt:**
```
You are a Twitch stream producer. Chat activity is low.

Current situation:
- Messages in last 30s: 1
- Last message: "lol" (from RegularViewer)
- Current game/activity: Playing Dark Souls
- Time since last streamer question: ~5 minutes

Suggest ONE specific question or topic to engage chat. Requirements:
- Related to current activity
- Easy to answer (not complex)
- Encourages multiple responses
- Under 30 words

Examples:
- "Ask chat: What boss should I fight next?"
- "Say: Chat, should I go left or right here?"
- "Try: Who's beaten this area before? Any tips?"
```

**Expected Output:**
> "Chat is quiet. Ask: 'Should I explore this area or move forward? What do you think?'"

---

### 3. Speaking Too Fast

**Context:**
- Words per minute > 220
- Clarity suffering

**Prompt:**
```
You are a Twitch stream producer focused on delivery quality.

Voice analysis:
- Current pace: 265 words per minute
- Target range: 100-220 WPM
- Energy level: High (0.8/1.0)

The streamer is speaking too quickly, likely due to excitement or nervousness.

Provide a brief, encouraging tip to slow down. Requirements:
- Acknowledge the energy (positive)
- Suggest specific technique (breathing, pauses)
- Under 25 words
- Not discouraging

Example: "Great energy, but slow down - you're at 265 WPM. Take a breath between thoughts for clarity."
```

**Expected Output:**
> "You're speaking fast at 265 words per minute. Take a breath between sentences to help viewers follow along."

---

### 4. Excessive Filler Words

**Context:**
- More than 10 "um", "uh", "like" per minute
- Sounds less confident

**Prompt:**
```
You are a Twitch stream producer helping with speech clarity.

Filler word analysis:
- Count in last 60s: 14 instances
- Types detected: "um" (8x), "like" (4x), "uh" (2x)
- Total words spoken: ~180

The streamer is using filler words frequently, which can reduce perceived confidence.

Provide a gentle, actionable tip. Requirements:
- NOT discouraging or negative
- Specific technique (pause instead, slow down, etc.)
- Under 30 words
- Frame positively

AVOID: "Stop saying um" or "You're saying um too much"
PREFER: "Try pausing instead of..." or "Reduce fillers by..."

```

**Expected Output:**
> "You've said 'um' 14 times in the last minute. Try pausing silently instead - it sounds more confident!"

---

### 5. Low Energy / Monotone

**Context:**
- Energy level < 0.3
- Pitch variation low
- May sound disengaged

**Prompt:**
```
You are a Twitch stream producer focused on presentation energy.

Voice metrics:
- Energy level: 0.2/1.0 (very low)
- Average pitch: 110 Hz (monotone)
- Words per minute: 95 (slow)

The streamer sounds low-energy or tired. This may disengage viewers.

Suggest ways to increase energy. Requirements:
- Encouraging tone (may actually be tired)
- Specific actions (stand up, react more, music, etc.)
- Under 35 words
- Acknowledge it's okay to have low moments

Examples:
- "Energy seems low. Try standing up or taking a quick break to recharge!"
- "You sound a bit tired. React out loud to what's happening on screen!"
```

**Expected Output:**
> "Your energy is a bit low. Try standing up or reacting more vocally to in-game events to re-engage!"

---

### 6. Potential Scammer Detected

**Context:**
- Message contains suspicious patterns
- Regex matched promo links, "check my profile", etc.

**Prompt:**
```
You are a Twitch stream producer with chat moderation duties.

Suspicious message detected:
- Username: SuspiciousBot47
- Message: "Hey check out my profile for free subs! link.sketchy.com/promo"
- Pattern matches: promotional links, too good to be true

This appears to be a scam/spam message.

Provide a brief alert. Requirements:
- Clear and direct
- Suggest action (ignore, ban, etc.)
- Under 20 words

```

**Expected Output:**
> "Likely scammer detected: SuspiciousBot47. Ignore that message and consider banning."

---

### 7. General Stream Check (No Specific Issue)

**Context:**
- No triggers fired
- Regular interval check
- Everything seems okay

**Prompt:**
```
You are a Twitch stream producer doing a routine check.

Current status:
- Chat activity: Normal (5 msgs/30s)
- Voice metrics: Good (175 WPM, 3 fillers, energy 0.7)
- No red flags detected

Provide one general improvement tip or encouragement. Requirements:
- Positive/encouraging
- Actionable if possible
- Can be about content, engagement, or technique
- Under 40 words

Examples:
- "Everything's looking good! Consider asking chat about their favorite moment so far."
- "Stream is running smoothly. Try mixing in a poll or question to keep engagement high."
```

**Expected Output:**
> "Everything looks great! Stream pacing is good. Consider asking chat what they'd like to see next."

---

## Customizing System Prompts

### Current Default (Concise & Actionable)

```python
system_msg = (
    "You are a Twitch stream producer. "
    "Give 1-2 actionable, encouraging tips in under 50 words. "
    "Be specific and concise. Focus on the most important issue."
)
```

### Alternative: Friendly & Casual

```python
system_msg = (
    "You're a friendly stream producer helping a Twitch streamer. "
    "Keep it casual and supportive - max 40 words. "
    "Give specific tips they can act on right now."
)
```

### Alternative: Professional & Detailed

```python
system_msg = (
    "You are an expert broadcast producer for live streaming. "
    "Analyze the provided metrics and give precise, professional feedback. "
    "Prioritize viewer retention and engagement. Max 60 words."
)
```

### Alternative: Energetic Coach

```python
system_msg = (
    "You're an energetic streaming coach! "
    "Pump up the streamer with actionable tips. "
    "Be enthusiastic but specific. Under 35 words, let's go!"
)
```

## Editing Prompts in Code

To customize prompts, edit [modules/ai_producer.py](modules/ai_producer.py):

```python
def _build_prompt(self, chat_data, voice_data, new_users, recent_messages):
    # System message (personality)
    system_msg = "YOUR CUSTOM SYSTEM MESSAGE HERE"
    
    # Context building (add/remove sections as needed)
    context_parts = []
    
    # ... existing code ...
    
    # Custom sections
    if custom_condition:
        context_parts.append("Custom context here")
    
    # Build final prompt
    prompt = "\n".join([
        "CONTEXT:",
        "\n".join(context_parts),
        "",
        "YOUR CUSTOM INSTRUCTION HERE:"
    ])
    
    return prompt
```

## Advanced: Template-Based Prompts

For scenario-specific prompts, use template dictionary:

```python
TEMPLATES = {
    "new_chatter": """
    New viewer alert: {username}
    Their message: {message}
    
    Suggest a warm welcome (under 30 words):
    """,
    
    "slow_chat": """
    Chat activity: {msg_count} messages in 30s (low)
    Current game: {game_name}
    
    Suggest an engaging question (under 35 words):
    """
}

# Use in generate_feedback()
if new_users:
    template = TEMPLATES["new_chatter"]
    prompt = template.format(
        username=new_users[0],
        message=recent_messages[-1].message if recent_messages else "..."
    )
```

## Tips for Writing Effective Prompts

1. **Be Specific**: "Give a welcome message" → "Give a 30-word welcome using their username and referencing their message"

2. **Set Constraints**: Always include word limits (Ollama tends to ramble without them)

3. **Provide Examples**: Show the AI what good output looks like

4. **Frame Positively**: "Reduce fillers" > "Stop saying um"

5. **Test Iteratively**: Generate feedback, review output, adjust prompt, repeat

6. **Context Matters**: Include relevant recent messages, not just metrics

7. **Avoid Ambiguity**: "Be helpful" is vague, "Suggest one specific question to ask chat" is clear

## Debugging Prompts

Enable debug logging to see generated prompts:

```python
# In main.py or config
logging.getLogger('modules.ai_producer').setLevel(logging.DEBUG)
```

Check logs for:
```
DEBUG - Generated prompt:
CONTEXT:
...
```

Review what the AI receives and adjust accordingly.

---

For more examples and community templates, see the project wiki or discussions.
