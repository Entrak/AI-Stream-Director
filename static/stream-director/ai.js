export class AIClient {
  constructor(baseUrl = "") {
    this.baseUrl = baseUrl;
    this.timeoutMs = 20000;
  }

  async generatePepTalk(lastSummary) {
    if (!lastSummary || !lastSummary.trim()) {
      return "No previous summary found yet. Focus on clear speech, pacing, and confident delivery today.";
    }

    try {
      const result = await this.postJson("/api/ai/pep-talk", { lastSummary });
      return result.text || "Keep momentum: focus on clarity, engagement, and a steady energy curve.";
    } catch (error) {
      console.warn("Pep talk fallback used:", error);
      return "Use your last stream lessons: reduce filler words, narrate your decisions, and keep chat involved every few minutes.";
    }
  }

  async generatePlanSuggestions(planText) {
    if (!planText || !planText.trim()) {
      return "Add a plan first: opener, core segment, interaction moments, and a strong closing.";
    }

    try {
      const result = await this.postJson("/api/ai/pre-stream-plan", { planText });
      return result.text || "Suggested: 1) Hook intro 2) Main content blocks 3) Chat checkpoints 4) Clear stream goal recap.";
    } catch (error) {
      console.warn("Plan suggestion fallback used:", error);
      return "Fallback plan: set one measurable goal, define 2-3 segments, and schedule chat check-ins every 10 minutes.";
    }
  }

  async analyzeTranscript(transcript, metrics = {}) {
    if (!transcript || transcript.trim().length < 30) {
      return "Need more speech before analysis. Keep narrating your actions clearly.";
    }

    try {
      const result = await this.postJson("/api/ai/during-analysis", { transcript, metrics });
      return result.text || "Speech check: maintain consistent volume and reduce filler phrases.";
    } catch (error) {
      console.warn("Transcript analysis fallback used:", error);
      return "Producer note: speak closer to mic, slow down slightly, and avoid long dead air gaps.";
    }
  }

  async analyzeSensitiveMessage(message, username) {
    if (!message || !message.trim()) {
      return { sensitive: false, suggestion: "" };
    }

    try {
      const result = await this.postJson("/api/ai/sensitive-topic-check", { message, username });
      return {
        sensitive: Boolean(result.sensitive),
        suggestion: result.suggestion || "Keep boundaries and redirect to a safe topic.",
      };
    } catch (error) {
      console.warn("Sensitive topic fallback used:", error);
      return {
        sensitive: false,
        suggestion: "",
      };
    }
  }

  async generateRaidChecklist(payload) {
    try {
      const result = await this.postJson("/api/ai/raid-welcome", payload);
      return result.bullets || [
        "Welcome raiders and thank the raiding streamer.",
        "Quickly introduce who you are and what stream this is.",
        "Recap current game/activity and invite chat to join.",
      ];
    } catch (error) {
      console.warn("Raid checklist fallback used:", error);
      return [
        "Welcome everyone and thank the raid.",
        "State stream name/type and current game.",
        "Invite new viewers to follow and say hi in chat.",
      ];
    }
  }

  async generatePostSummary(payload) {
    try {
      const result = await this.postJson("/api/ai/post-summary", payload);
      return result.text || "Session complete. Reflect on pacing, clarity, engagement consistency, and recovery moments.";
    } catch (error) {
      console.warn("Post summary fallback used:", error);
      return "Post-stream note: identify one speaking improvement and one engagement tactic to practice next stream.";
    }
  }

  async postJson(path, body) {
    const controller = new AbortController();
    const timeoutHandle = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok) {
        throw new Error(`Request failed (${response.status})`);
      }

      const data = await response.json();
      if (data && typeof data === "object") {
        if ("ok" in data && data.ok === false) {
          throw new Error(data.error || "AI endpoint returned failure");
        }

        if ("data" in data) {
          return data.data;
        }
      }

      return data;
    } finally {
      clearTimeout(timeoutHandle);
    }
  }
}