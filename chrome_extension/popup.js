const API_BASE = "http://127.0.0.1:5050";

const queryEl = document.getElementById("query");
const askBtn = document.getElementById("askBtn");
const pageBtn = document.getElementById("pageBtn");
const listenBtn = document.getElementById("listenBtn");
const stopBtn = document.getElementById("stopBtn");
const autoSpeakEl = document.getElementById("autoSpeak");
const wakeModeEl = document.getElementById("wakeMode");
const wakeStartBtn = document.getElementById("wakeStartBtn");
const wakePingBtn = document.getElementById("wakePingBtn");
const statusEl = document.getElementById("status");

let recognition = null;

function setStatus(text) {
  statusEl.textContent = text;
}

function loadWakeModeSetting() {
  chrome.storage.local.get({ wakeModeEnabled: true }, (data) => {
    wakeModeEl.checked = Boolean(data.wakeModeEnabled);
  });
}

function bindWakeModeSetting() {
  wakeModeEl.addEventListener("change", () => {
    const enabled = wakeModeEl.checked;
    chrome.storage.local.set({ wakeModeEnabled: enabled }, () => {
      setStatus(
        enabled
          ? "Wake mode enabled. Say: activate voice assistant"
          : "Wake mode disabled."
      );
    });
  });
}

async function sendToActiveTab(message) {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tabs.length) {
    throw new Error("No active tab found.");
  }
  return chrome.tabs.sendMessage(tabs[0].id, message);
}

async function startWakeFromPopup() {
  try {
    await sendToActiveTab({ type: "NOVA_WAKE_START" });
    setStatus("Wake listener start requested. Now say: activate voice assistant");
  } catch (err) {
    setStatus(`Cannot start wake listener: ${err.message}. Refresh the page tab and try again.`);
  }
}

async function pingWakeStatus() {
  try {
    const data = await sendToActiveTab({ type: "NOVA_WAKE_PING" });
    setStatus(`Wake status: ${data?.status || "No response"}`);
  } catch (err) {
    setStatus(`Wake status unavailable: ${err.message}. Refresh the page tab and try again.`);
  }
}

async function autoStartWakeOnPopupOpen() {
  chrome.storage.local.get({ wakeModeEnabled: true }, async (data) => {
    if (!data.wakeModeEnabled) {
      setStatus("Wake mode is off. You can enable it again from this toggle.");
      return;
    }

    try {
      await sendToActiveTab({ type: "NOVA_WAKE_START" });
      const wakeData = await sendToActiveTab({ type: "NOVA_WAKE_PING" });
      setStatus(`Wake status: ${wakeData?.status || "Wake started"}`);
    } catch (err) {
      setStatus(`Auto-start failed on this tab: ${err.message}. Refresh the page once.`);
    }
  });
}

function speak(text) {
  if (!autoSpeakEl.checked) return;
  if (!("speechSynthesis" in window)) return;
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1;
  utterance.pitch = 1;
  window.speechSynthesis.cancel();
  window.speechSynthesis.speak(utterance);
}

async function callBackend(path, payload) {
  const response = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `Request failed with status ${response.status}`);
  }
  return data;
}

async function askText() {
  const query = queryEl.value.trim();
  if (!query) {
    setStatus("Type a request first.");
    return;
  }

  setStatus("Thinking...");
  try {
    const data = await callBackend("/ask", { query });
    setStatus(data.reply);
    speak(data.reply);
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  }
}

async function captureVisibleTab() {
  const tabDataUrl = await chrome.tabs.captureVisibleTab(undefined, {
    format: "png"
  });
  return tabDataUrl;
}

async function describePage() {
  const query = queryEl.value.trim() || "describe this page";
  setStatus("Capturing page and analyzing...");

  try {
    const imageDataUrl = await captureVisibleTab();
    const data = await callBackend("/describe-image", {
      query,
      image_data_url: imageDataUrl
    });
    setStatus(data.reply);
    speak(data.reply);
  } catch (err) {
    setStatus(`Error: ${err.message}`);
  }
}

function initVoiceInput() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) {
    setStatus("Voice input not available in this Chrome build.");
    listenBtn.disabled = true;
    stopBtn.disabled = true;
    return;
  }

  recognition = new SpeechRecognition();
  recognition.lang = "en-US";
  recognition.continuous = false;
  recognition.interimResults = true;

  recognition.onstart = () => {
    setStatus("Listening...");
    listenBtn.disabled = true;
    stopBtn.disabled = false;
  };

  recognition.onresult = (event) => {
    const transcript = Array.from(event.results)
      .map(result => result[0].transcript)
      .join(" ")
      .trim();
    queryEl.value = transcript;
    if (event.results[event.results.length - 1].isFinal) {
      setStatus(`Heard: ${transcript}`);
    }
  };

  recognition.onerror = (event) => {
    setStatus(`Voice error: ${event.error}`);
    listenBtn.disabled = false;
    stopBtn.disabled = true;
  };

  recognition.onend = () => {
    listenBtn.disabled = false;
    stopBtn.disabled = true;
  };
}

askBtn.addEventListener("click", askText);
pageBtn.addEventListener("click", describePage);
listenBtn.addEventListener("click", () => recognition && recognition.start());
stopBtn.addEventListener("click", () => recognition && recognition.stop());
wakeStartBtn.addEventListener("click", startWakeFromPopup);
wakePingBtn.addEventListener("click", pingWakeStatus);

initVoiceInput();
loadWakeModeSetting();
bindWakeModeSetting();
autoStartWakeOnPopupOpen();
