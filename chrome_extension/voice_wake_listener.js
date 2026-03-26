(() => {
  const API_BASE = "http://127.0.0.1:5050";
  const WAKE_PHRASES = [
    "activate voice assistant",
    "activate voices assistant",
    "activate voice assistent",
    "activate voices assistent"
  ];

  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition || !window.speechSynthesis || !window.chrome?.storage?.local) {
    if (window.chrome?.storage?.local) {
      chrome.storage.local.set({
        wakeDebugStatus: "Wake listener unavailable on this page/browser."
      });
    }
    return;
  }

  let wakeRecognizer = null;
  let enabled = true;
  let speaking = false;
  let wakeLoopActive = false;

  function setWakeStatus(text) {
    chrome.storage.local.set({ wakeDebugStatus: text });
  }

  function normalize(text) {
    return (text || "").toLowerCase().replace(/[^a-z0-9 ]/g, " ").replace(/\s+/g, " ").trim();
  }

  async function postJSON(path, payload) {
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

  function containsWakePhrase(normalizedText) {
    return WAKE_PHRASES.some((phrase) => normalizedText.includes(phrase));
  }

  function isExitCommand(normalizedText) {
    return (
      normalizedText === "exit" ||
      normalizedText === "quit" ||
      normalizedText === "stop" ||
      normalizedText.includes("exit command") ||
      normalizedText.includes("turn off assistant") ||
      normalizedText.includes("disable wake mode") ||
      normalizedText.includes("go to sleep")
    );
  }

  function isScreenDescriptionQuery(normalizedText) {
    const screenKeywords = [
      "screen",
      "display",
      "monitor",
      "on my screen",
      "in my screen",
      "what is on my screen",
      "what is in my screen",
      "what's on my screen",
      "what's in my screen"
    ];
    const describeKeywords = ["describe", "what", "read", "tell", "see"];
    return screenKeywords.some((k) => normalizedText.includes(k)) &&
           describeKeywords.some((k) => normalizedText.includes(k));
  }

  async function requestScreenDescription(query) {
    return new Promise((resolve, reject) => {
      chrome.runtime.sendMessage(
        { type: "NOVA_DESCRIBE_SCREEN", query },
        (response) => {
          if (chrome.runtime.lastError) {
            reject(new Error(chrome.runtime.lastError.message));
          } else if (response && response.ok) {
            resolve(response.reply);
          } else if (response && !response.ok) {
            reject(new Error(response.error || "Background error"));
          } else {
            reject(new Error("No response from background"));
          }
        }
      );
    });
  }

  function speak(text) {
    return new Promise((resolve) => {
      if (!enabled) {
        resolve();
        return;
      }
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1;
      utterance.pitch = 1;
      utterance.onstart = () => {
        speaking = true;
      };
      utterance.onend = () => {
        speaking = false;
        resolve();
      };
      utterance.onerror = () => {
        speaking = false;
        resolve();
      };
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(utterance);
    });
  }

  function stopWakeLoop() {
    wakeLoopActive = false;
    if (wakeRecognizer) {
      try {
        wakeRecognizer.onend = null;
        wakeRecognizer.stop();
      } catch (_) {
      }
    }
  }

  function createWakeRecognizer() {
    const recognizer = new SpeechRecognition();
    recognizer.lang = "en-US";
    recognizer.continuous = true;
    recognizer.interimResults = false;

    recognizer.onresult = async (event) => {
      if (!enabled || speaking) {
        return;
      }

      const heard = Array.from(event.results)
        .slice(event.resultIndex)
        .map((result) => result[0]?.transcript || "")
        .join(" ")
        .trim();

      const normalized = normalize(heard);
      if (!containsWakePhrase(normalized)) {
        return;
      }

      setWakeStatus(`Wake phrase detected: ${heard}`);
      stopWakeLoop();
      await speak("How can I help you?");
      await listenForCommandAndRespond();

      if (enabled) {
        startWakeLoop();
      }
    };

    recognizer.onerror = () => {
      setWakeStatus("Wake listener error. Retrying...");
      if (enabled && wakeLoopActive) {
        setTimeout(startWakeLoop, 600);
      }
    };

    recognizer.onend = () => {
      if (enabled && wakeLoopActive) {
        setTimeout(startWakeLoop, 400);
      }
    };

    return recognizer;
  }

  async function listenForCommandAndRespond() {
    setWakeStatus("Listening for command...");
    const command = await new Promise((resolve) => {
      const commandRecognizer = new SpeechRecognition();
      commandRecognizer.lang = "en-US";
      commandRecognizer.continuous = false;
      commandRecognizer.interimResults = false;

      let transcript = "";
      commandRecognizer.onresult = (event) => {
        transcript = Array.from(event.results)
          .map((result) => result[0]?.transcript || "")
          .join(" ")
          .trim();
      };

      commandRecognizer.onerror = () => {
        resolve("");
      };

      commandRecognizer.onend = () => {
        resolve(transcript);
      };

      try {
        commandRecognizer.start();
      } catch (_) {
        resolve("");
      }
    });

    if (!command) {
      setWakeStatus("No command captured.");
      await speak("I did not catch that.");
      return;
    }

    const normalizedCommand = normalize(command);
    if (isExitCommand(normalizedCommand)) {
      enabled = false;
      wakeLoopActive = false;
      chrome.storage.local.set({
        wakeModeEnabled: false,
        wakeDebugStatus: "Wake mode disabled by voice command."
      });
      await speak("Wake mode turned off. Turn it on from extension when needed.");
      return;
    }

    if (isScreenDescriptionQuery(normalizedCommand)) {
      setWakeStatus("Capturing screen...");
      try {
        const reply = await requestScreenDescription(command);
        setWakeStatus("Screen described.");
        await speak(reply || "I could not describe your screen.");
      } catch (error) {
        setWakeStatus(`Screen description error: ${error.message}`);
        await speak(`I cannot capture your screen. ${error.message}`);
      }
      return;
    }

    setWakeStatus(`Command: ${command}`);
    try {
      const response = await postJSON("/ask", { query: command });
      setWakeStatus("Response received from backend.");
      await speak(response.reply || "I have no response.");
    } catch (error) {
      setWakeStatus(`Backend error: ${error.message}`);
      await speak(`I cannot reach backend. ${error.message}`);
    }
  }

  function startWakeLoop() {
    if (!enabled || wakeLoopActive || document.hidden) {
      return;
    }

    wakeLoopActive = true;
    setWakeStatus("Wake listener active. Say: activate voice assistant");
    wakeRecognizer = createWakeRecognizer();
    try {
      wakeRecognizer.start();
    } catch (_) {
      wakeLoopActive = false;
      setWakeStatus("Microphone not allowed yet. Open extension popup once or interact with the page.");
      setTimeout(startWakeLoop, 800);
    }
  }

  function syncEnabledFromStorage() {
    chrome.storage.local.get({ wakeModeEnabled: true }, (data) => {
      enabled = Boolean(data.wakeModeEnabled);
      if (enabled) {
        setWakeStatus("Wake mode enabled. Initializing...");
        startWakeLoop();
      } else {
        stopWakeLoop();
        setWakeStatus("Wake mode disabled.");
      }
    });
  }

  chrome.storage.onChanged.addListener((changes, areaName) => {
    if (areaName !== "local" || !changes.wakeModeEnabled) {
      return;
    }
    enabled = Boolean(changes.wakeModeEnabled.newValue);
    if (enabled) {
      setWakeStatus("Wake mode enabled. Initializing...");
      startWakeLoop();
    } else {
      stopWakeLoop();
      setWakeStatus("Wake mode disabled.");
    }
  });

  chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    if (!message || typeof message !== "object") {
      return;
    }

    if (message.type === "NOVA_WAKE_START") {
      enabled = true;
      chrome.storage.local.set({ wakeModeEnabled: true }, () => {
        setWakeStatus("Wake mode force-start requested from popup.");
        startWakeLoop();
        sendResponse({ ok: true });
      });
      return true;
    }

    if (message.type === "NOVA_WAKE_PING") {
      chrome.storage.local.get({ wakeDebugStatus: "No status yet." }, (data) => {
        sendResponse({ ok: true, status: data.wakeDebugStatus });
      });
      return true;
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopWakeLoop();
    } else if (enabled) {
      startWakeLoop();
    }
  });

  document.addEventListener("click", () => {
    if (enabled && !wakeLoopActive) {
      startWakeLoop();
    }
  }, { passive: true });

  syncEnabledFromStorage();
})();
