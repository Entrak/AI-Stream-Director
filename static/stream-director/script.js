import { AIClient } from "./ai.js";
import { SpeechManager } from "./speech.js";
import { ChatManager } from "./chat.js";

const STORAGE_KEYS = {
  lastStreamSummary: "asd:lastStreamSummary:v1",
  sessionHistory: "asd:sessionHistory:v1",
};

const appState = {
  stage: "pre",
  stageRunning: false,
  aiStatusTimer: null,
  transcript: "",
  speechAnalysisTimer: null,
  raidTimerInterval: null,
  raidSecondsLeft: 0,
  chatEvents: [],
  transcriptChunks: [],
  speechMetrics: {
    wordCount: 0,
    fillerCount: 0,
    fillerRate: 0,
  },
  setupChecks: {
    mic: "unknown",
    camera: "unknown",
  },
};

const ui = {
  stagePreBtn: document.getElementById("stagePreBtn"),
  stageDuringBtn: document.getElementById("stageDuringBtn"),
  stagePostBtn: document.getElementById("stagePostBtn"),
  startStageBtn: document.getElementById("startStageBtn"),
  stopStageBtn: document.getElementById("stopStageBtn"),
  stagePill: document.getElementById("stagePill"),
  micStatus: document.getElementById("micStatus"),
  cameraStatus: document.getElementById("cameraStatus"),
  speechApiStatus: document.getElementById("speechApiStatus"),
  chatStatus: document.getElementById("chatStatus"),
  aiSelfTestBtn: document.getElementById("aiSelfTestBtn"),
  aiStatus: document.getElementById("aiStatus"),
  aiProviderStatus: document.getElementById("aiProviderStatus"),
  preStreamPanel: document.getElementById("preStreamPanel"),
  duringStreamPanel: document.getElementById("duringStreamPanel"),
  postStreamPanel: document.getElementById("postStreamPanel"),
  lastSummaryInput: document.getElementById("lastSummaryInput"),
  loadSummaryBtn: document.getElementById("loadSummaryBtn"),
  pepTalkOutput: document.getElementById("pepTalkOutput"),
  generatePepTalkBtn: document.getElementById("generatePepTalkBtn"),
  planInput: document.getElementById("planInput"),
  planOutput: document.getElementById("planOutput"),
  generatePlanBtn: document.getElementById("generatePlanBtn"),
  runSetupChecksBtn: document.getElementById("runSetupChecksBtn"),
  setupCheckOutput: document.getElementById("setupCheckOutput"),
  startListeningBtn: document.getElementById("startListeningBtn"),
  stopListeningBtn: document.getElementById("stopListeningBtn"),
  connectChatBtn: document.getElementById("connectChatBtn"),
  manualRaidBtn: document.getElementById("manualRaidBtn"),
  transcriptOutput: document.getElementById("transcriptOutput"),
  speechFeedbackOutput: document.getElementById("speechFeedbackOutput"),
  twitchUsernameInput: document.getElementById("twitchUsernameInput"),
  twitchTokenInput: document.getElementById("twitchTokenInput"),
  twitchChannelInput: document.getElementById("twitchChannelInput"),
  raidChecklistOutput: document.getElementById("raidChecklistOutput"),
  raidTimer: document.getElementById("raidTimer"),
  endStreamBtn: document.getElementById("endStreamBtn"),
  sessionDataOutput: document.getElementById("sessionDataOutput"),
  postSummaryOutput: document.getElementById("postSummaryOutput"),
  producerFeed: document.getElementById("producerFeed"),
};

const ai = new AIClient("");
const speech = new SpeechManager({
  onTranscript: handleSpeechChunk,
  onError: (error) => addFeed(`Speech error: ${error.message}`, "warning", "speech"),
  onStateChange: (running) => {
    addFeed(running ? "Speech recognition started." : "Speech recognition stopped.", "normal", "speech");
  },
});
const chat = new ChatManager({
  onMessage: handleChatMessage,
  onRaid: handleRaidEvent,
  onError: (error) => addFeed(`Chat error: ${error.message}`, "warning", "chat"),
  onStateChange: (connected) => {
    ui.chatStatus.textContent = connected ? "connected" : "disconnected";
  },
});

initialize();

function initialize() {
  ui.speechApiStatus.textContent = speech.isSupported() ? "supported" : "unsupported";
  attachEventListeners();
  setStage("pre");
  loadStoredSummary();
  refreshAIStatus();
  appState.aiStatusTimer = window.setInterval(refreshAIStatus, 15000);
  addFeed("AI Stream Director initialized.", "normal", "system");
}

async function refreshAIStatus() {
  try {
    const response = await fetch("/api/ai/status", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`status ${response.status}`);
    }

    const payload = await response.json();
    const data = payload?.data || {};

    if (data.ai_live) {
      ui.aiStatus.textContent = "live";
      ui.aiProviderStatus.textContent = data.active_provider_name || "available";
      return;
    }

    ui.aiStatus.textContent = "fallback mode";
    ui.aiProviderStatus.textContent = "none available";
  } catch (_error) {
    ui.aiStatus.textContent = "unreachable";
    ui.aiProviderStatus.textContent = "status check failed";
  }
}

async function runAISelfTest() {
  ui.aiSelfTestBtn.disabled = true;
  const originalText = ui.aiSelfTestBtn.textContent;
  ui.aiSelfTestBtn.textContent = "Testing...";

  try {
    const response = await fetch("/api/ai/status", { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`status ${response.status}`);
    }

    const payload = await response.json();
    const data = payload?.data || {};
    const providerName = data.active_provider_name || "none";

    await refreshAIStatus();

    if (data.ai_live) {
      addFeed(`AI self-test passed. Live provider: ${providerName}.`, "normal", "ai");
      return;
    }

    const fallbackReason = data.fallback_mode ? "fallback mode active" : "provider unavailable";
    addFeed(`AI self-test failed. ${fallbackReason}.`, "warning", "ai");
  } catch (error) {
    addFeed(`AI self-test error: ${error.message}`, "critical", "ai");
  } finally {
    ui.aiSelfTestBtn.disabled = false;
    ui.aiSelfTestBtn.textContent = originalText;
  }
}

function attachEventListeners() {
  ui.stagePreBtn.addEventListener("click", () => setStage("pre"));
  ui.stageDuringBtn.addEventListener("click", () => setStage("during"));
  ui.stagePostBtn.addEventListener("click", () => setStage("post"));

  ui.startStageBtn.addEventListener("click", () => {
    appState.stageRunning = true;
    addFeed(`${appState.stage} stage started.`, "normal", "stage");
  });

  ui.stopStageBtn.addEventListener("click", () => {
    appState.stageRunning = false;
    if (appState.stage === "during") {
      stopDuringStageWorkers();
    }
    addFeed(`${appState.stage} stage stopped.`, "warning", "stage");
  });

  ui.loadSummaryBtn.addEventListener("click", loadStoredSummary);
  ui.generatePepTalkBtn.addEventListener("click", generatePepTalk);
  ui.generatePlanBtn.addEventListener("click", generatePlanSuggestions);
  ui.runSetupChecksBtn.addEventListener("click", runSetupChecks);
  ui.aiSelfTestBtn.addEventListener("click", runAISelfTest);

  ui.startListeningBtn.addEventListener("click", startSpeechLoop);
  ui.stopListeningBtn.addEventListener("click", stopSpeechLoop);
  ui.connectChatBtn.addEventListener("click", connectChat);
  ui.manualRaidBtn.addEventListener("click", () => chat.triggerManualRaid(20));

  ui.endStreamBtn.addEventListener("click", endStreamAndGenerateSummary);
}

function setStage(stage) {
  appState.stage = stage;
  appState.stageRunning = false;

  ui.stagePreBtn.classList.toggle("active", stage === "pre");
  ui.stageDuringBtn.classList.toggle("active", stage === "during");
  ui.stagePostBtn.classList.toggle("active", stage === "post");

  ui.preStreamPanel.classList.toggle("hidden", stage !== "pre");
  ui.duringStreamPanel.classList.toggle("hidden", stage !== "during");
  ui.postStreamPanel.classList.toggle("hidden", stage !== "post");

  ui.stagePill.textContent = `${stage}-stream`;
  ui.stagePill.className = "status-pill safe";

  if (stage !== "during") {
    stopDuringStageWorkers();
  }
}

function loadStoredSummary() {
  const summary = localStorage.getItem(STORAGE_KEYS.lastStreamSummary) || "";
  ui.lastSummaryInput.value = summary;
  if (!summary) {
    ui.pepTalkOutput.textContent = "No saved post-stream summary yet. End a stream once to build historical coaching context.";
  }
}

async function generatePepTalk() {
  ui.pepTalkOutput.textContent = "Generating pep talk...";
  const summary = ui.lastSummaryInput.value.trim();
  const pepTalk = await ai.generatePepTalk(summary);
  ui.pepTalkOutput.textContent = pepTalk;
  addFeed("Pre-stream pep talk updated.", "normal", "pre");
}

async function generatePlanSuggestions() {
  ui.planOutput.textContent = "Analyzing plan...";
  const plan = ui.planInput.value.trim();
  const suggestions = await ai.generatePlanSuggestions(plan);
  ui.planOutput.textContent = suggestions;
  addFeed("Pre-stream plan suggestions generated.", "normal", "pre");
}

async function runSetupChecks() {
  ui.setupCheckOutput.textContent = "Running setup checks...";

  const checks = [];

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    appState.setupChecks.mic = "failed";
    appState.setupChecks.camera = "failed";
    ui.micStatus.textContent = "unsupported";
    ui.cameraStatus.textContent = "unsupported";
    ui.setupCheckOutput.textContent = "Media device APIs are unavailable in this browser context.";
    addFeed("Setup checks unavailable: mediaDevices API missing.", "warning", "pre");
    return;
  }

  try {
    const micStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    appState.setupChecks.mic = "ok";
    ui.micStatus.textContent = "ok";
    checks.push("Microphone permission granted.");

    const audioTracks = micStream.getAudioTracks();
    checks.push(audioTracks.length > 0 ? "Audio input detected." : "No audio input track found.");
    micStream.getTracks().forEach((track) => track.stop());
  } catch (error) {
    appState.setupChecks.mic = "failed";
    ui.micStatus.textContent = "failed";
    checks.push(`Microphone check failed: ${error.message}`);
  }

  try {
    const cameraStream = await navigator.mediaDevices.getUserMedia({ video: true });
    appState.setupChecks.camera = "ok";
    ui.cameraStatus.textContent = "ok";
    checks.push("Camera/VTuber source permission granted.");
    cameraStream.getTracks().forEach((track) => track.stop());
  } catch (error) {
    appState.setupChecks.camera = "failed";
    ui.cameraStatus.textContent = "failed";
    checks.push(`Camera check failed: ${error.message}`);
  }

  ui.setupCheckOutput.textContent = checks.join("\n");
  addFeed("Pre-stream setup checks completed.", "normal", "pre");
}

function startSpeechLoop() {
  if (appState.stage !== "during") {
    setStage("during");
  }

  speech.start();
  startSpeechAnalysisTimer();
}

function stopSpeechLoop() {
  speech.stop();
  clearSpeechAnalysisTimer();
}

function startSpeechAnalysisTimer() {
  clearSpeechAnalysisTimer();
  appState.speechAnalysisTimer = window.setInterval(async () => {
    const recentBuffer = speech.takeRecentBuffer();
    if (!recentBuffer.trim()) {
      return;
    }
    const feedback = await ai.analyzeTranscript(recentBuffer, appState.speechMetrics);
    ui.speechFeedbackOutput.textContent = feedback;
    addFeed(feedback, "normal", "speech");
  }, 30000);
}

function clearSpeechAnalysisTimer() {
  if (appState.speechAnalysisTimer) {
    window.clearInterval(appState.speechAnalysisTimer);
    appState.speechAnalysisTimer = null;
  }
}

function handleSpeechChunk(payload) {
  appState.transcript = payload.fullTranscript;
  appState.transcriptChunks.push(payload.chunk);
  appState.speechMetrics = payload.metrics;

  ui.transcriptOutput.textContent = appState.transcript || "No transcript yet.";
}

async function connectChat() {
  const username = ui.twitchUsernameInput.value.trim();
  const token = ui.twitchTokenInput.value.trim();
  const channel = ui.twitchChannelInput.value.trim();

  await chat.connect({ username, token, channel });
  addFeed("Twitch chat connection attempt complete.", "normal", "chat");
}

async function handleChatMessage(chatMessage) {
  appState.chatEvents.push({
    type: "message",
    at: new Date().toISOString(),
    ...chatMessage,
  });

  const moderation = await ai.analyzeSensitiveMessage(chatMessage.message, chatMessage.username);
  if (moderation.sensitive) {
    const warningText = `Sensitive topic from ${chatMessage.username}. Suggestion: ${moderation.suggestion}`;
    addFeed(warningText, "critical", "chat");
  }
}

async function handleRaidEvent(raidEvent) {
  appState.chatEvents.push({
    type: "raid",
    at: new Date().toISOString(),
    ...raidEvent,
  });

  const checklist = await ai.generateRaidChecklist({
    streamName: "My Stream",
    streamerType: "Variety",
    currentGame: "Current Game",
    viewers: raidEvent.viewers || 0,
    raider: raidEvent.raider || "unknown",
  });

  ui.raidChecklistOutput.textContent = checklist.map((item, index) => `${index + 1}. ${item}`).join("\n");
  addFeed(`Raid detected (${raidEvent.viewers || 0} viewers). Deliver welcome summary now.`, "critical", "raid");
  startRaidTimer(45);
}

function startRaidTimer(durationSec) {
  if (appState.raidTimerInterval) {
    clearInterval(appState.raidTimerInterval);
  }

  appState.raidSecondsLeft = durationSec;
  renderRaidTimer();

  appState.raidTimerInterval = window.setInterval(() => {
    appState.raidSecondsLeft -= 1;
    renderRaidTimer();

    if (appState.raidSecondsLeft <= 0) {
      window.clearInterval(appState.raidTimerInterval);
      appState.raidTimerInterval = null;
      addFeed("Raid greeting timer complete.", "warning", "raid");
    }
  }, 1000);
}

function renderRaidTimer() {
  const safeSeconds = Math.max(appState.raidSecondsLeft, 0);
  const mins = String(Math.floor(safeSeconds / 60)).padStart(2, "0");
  const secs = String(safeSeconds % 60).padStart(2, "0");
  ui.raidTimer.textContent = `${mins}:${secs}`;
}

function stopDuringStageWorkers() {
  stopSpeechLoop();
  chat.disconnect();

  if (appState.raidTimerInterval) {
    window.clearInterval(appState.raidTimerInterval);
    appState.raidTimerInterval = null;
    ui.raidTimer.textContent = "00:00";
  }
}

async function endStreamAndGenerateSummary() {
  stopDuringStageWorkers();
  setStage("post");

  const sessionData = {
    transcript: appState.transcript,
    transcriptChunks: appState.transcriptChunks,
    speechMetrics: appState.speechMetrics,
    chatEvents: appState.chatEvents,
    setupChecks: appState.setupChecks,
    endedAt: new Date().toISOString(),
  };

  ui.sessionDataOutput.textContent = JSON.stringify(sessionData, null, 2);
  ui.postSummaryOutput.textContent = "Generating post-stream summary...";

  const summary = await ai.generatePostSummary(sessionData);
  ui.postSummaryOutput.textContent = summary;

  localStorage.setItem(STORAGE_KEYS.lastStreamSummary, summary);
  persistSessionRecord(sessionData, summary);

  addFeed("Post-stream summary generated and saved for next pre-stream pep talk.", "normal", "post");
}

function persistSessionRecord(sessionData, summary) {
  const current = JSON.parse(localStorage.getItem(STORAGE_KEYS.sessionHistory) || "[]");
  const next = [
    {
      timestamp: new Date().toISOString(),
      summary,
      metrics: sessionData.speechMetrics,
      chatEventCount: sessionData.chatEvents.length,
    },
    ...current,
  ].slice(0, 30);

  localStorage.setItem(STORAGE_KEYS.sessionHistory, JSON.stringify(next));
}

function addFeed(message, severity = "normal", source = "system") {
  const item = document.createElement("div");
  item.className = "feed-item";

  if (severity === "warning") {
    item.classList.add("warning");
  }

  if (severity === "critical") {
    item.classList.add("critical");
  }

  const content = document.createElement("div");
  content.textContent = message;

  const meta = document.createElement("div");
  meta.className = "feed-meta";
  meta.textContent = `${new Date().toLocaleTimeString()} · ${source}`;

  item.appendChild(content);
  item.appendChild(meta);

  ui.producerFeed.prepend(item);

  while (ui.producerFeed.children.length > 40) {
    ui.producerFeed.removeChild(ui.producerFeed.lastChild);
  }
}