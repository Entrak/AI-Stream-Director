export class ChatManager {
  constructor(options = {}) {
    this.onMessage = options.onMessage || (() => {});
    this.onRaid = options.onRaid || (() => {});
    this.onStateChange = options.onStateChange || (() => {});
    this.onError = options.onError || (() => {});
    this.client = null;
    this.connected = false;
  }

  async connect(config) {
    const { username, token, channel } = config;

    if (!window.tmi) {
      this.onError(new Error("tmi.js is not loaded. Include it to enable live Twitch chat."));
      return;
    }

    if (!username || !token || !channel) {
      this.onError(new Error("Twitch username, token, and channel are required."));
      return;
    }

    this.client = new window.tmi.Client({
      options: { debug: false },
      identity: {
        username,
        password: token,
      },
      channels: [channel],
      connection: {
        reconnect: true,
        secure: true,
      },
    });

    this.client.on("connected", () => {
      this.connected = true;
      this.onStateChange(true);
    });

    this.client.on("disconnected", (reason) => {
      this.connected = false;
      this.onStateChange(false);
      this.onError(new Error(`Twitch disconnected: ${reason || "unknown reason"}`));
    });

    this.client.on("message", (_channel, tags, message, self) => {
      if (self) {
        return;
      }

      const usernameTag = tags["display-name"] || tags.username || "unknown";
      this.onMessage({ username: usernameTag, message, tags });

      const lowered = String(message || "").toLowerCase();
      if (lowered.includes("raided") && lowered.includes("viewers")) {
        this.onRaid({
          raider: usernameTag,
          viewers: this.extractViewersFromMessage(lowered),
          source: "message-pattern",
        });
      }
    });

    this.client.on("notice", (_channel, _msgid, message) => {
      if (String(message || "").toLowerCase().includes("raid")) {
        this.onRaid({
          raider: "system",
          viewers: 0,
          source: "notice",
          message,
        });
      }
    });

    try {
      await this.client.connect();
    } catch (error) {
      this.onError(error);
    }
  }

  disconnect() {
    if (!this.client) {
      return;
    }

    this.client.disconnect().catch((error) => {
      this.onError(error);
    });

    this.connected = false;
    this.onStateChange(false);
  }

  triggerManualRaid(viewers = 20) {
    this.onRaid({
      raider: "manual",
      viewers,
      source: "manual",
    });
  }

  extractViewersFromMessage(message) {
    const match = message.match(/(\d+)\s+viewer/);
    if (!match) {
      return 0;
    }
    return Number.parseInt(match[1], 10) || 0;
  }
}