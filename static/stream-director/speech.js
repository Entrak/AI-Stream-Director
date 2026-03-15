export class SpeechManager {
  constructor(options = {}) {
    this.onTranscript = options.onTranscript || (() => {});
    this.onError = options.onError || (() => {});
    this.onStateChange = options.onStateChange || (() => {});
    this.recognition = null;
    this.isRunning = false;
    this.shouldRestart = false;
    this.fullTranscript = "";
    this.recentBuffer = "";
    this.fillerWords = ["um", "uh", "erm", "like", "you know"];
    this.fillerCount = 0;
    this.wordCount = 0;
  }

  isSupported() {
    return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition);
  }

  start() {
    if (!this.isSupported()) {
      this.onError(new Error("SpeechRecognition API not supported in this browser."));
      return;
    }

    if (this.isRunning) {
      return;
    }

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.recognition = new SpeechRecognition();
    this.recognition.continuous = true;
    this.recognition.interimResults = true;
    this.recognition.lang = "en-US";

    this.recognition.onresult = (event) => {
      let finalChunk = "";

      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index];
        const text = result[0]?.transcript || "";
        if (result.isFinal) {
          finalChunk += `${text} `;
        }
      }

      if (finalChunk.trim()) {
        this.consumeChunk(finalChunk.trim());
      }
    };

    this.recognition.onerror = (event) => {
      this.onError(new Error(`Speech recognition error: ${event.error}`));
    };

    this.recognition.onend = () => {
      this.isRunning = false;
      this.onStateChange(false);
      if (this.shouldRestart) {
        this.start();
      }
    };

    this.shouldRestart = true;
    this.recognition.start();
    this.isRunning = true;
    this.onStateChange(true);
  }

  stop() {
    this.shouldRestart = false;
    if (this.recognition && this.isRunning) {
      this.recognition.stop();
    }
    this.isRunning = false;
    this.onStateChange(false);
  }

  consumeChunk(text) {
    this.fullTranscript = `${this.fullTranscript} ${text}`.trim();
    this.recentBuffer = `${this.recentBuffer} ${text}`.trim();

    const words = text
      .toLowerCase()
      .replace(/[^a-z0-9\s']/g, " ")
      .split(/\s+/)
      .filter(Boolean);

    this.wordCount += words.length;
    this.fillerCount += words.filter((word) => this.fillerWords.includes(word)).length;

    this.onTranscript({
      chunk: text,
      fullTranscript: this.fullTranscript,
      metrics: this.getMetrics(),
    });
  }

  takeRecentBuffer() {
    const snapshot = this.recentBuffer;
    this.recentBuffer = "";
    return snapshot;
  }

  getMetrics() {
    const fillerRate = this.wordCount > 0 ? this.fillerCount / this.wordCount : 0;
    return {
      wordCount: this.wordCount,
      fillerCount: this.fillerCount,
      fillerRate,
    };
  }

  reset() {
    this.fullTranscript = "";
    this.recentBuffer = "";
    this.wordCount = 0;
    this.fillerCount = 0;
  }
}