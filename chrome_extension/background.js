const API_BASE = "http://127.0.0.1:5050";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || typeof message !== "object") {
    return;
  }

  if (message.type === "NOVA_DESCRIBE_SCREEN") {
    describeScreenForTab(_sender.tab.id, message.query || "describe this screen")
      .then((reply) => {
        sendResponse({ ok: true, reply });
      })
      .catch((error) => {
        sendResponse({ ok: false, error: error.message });
      });
    return true;
  }
});

async function describeScreenForTab(tabId, userQuery) {
  const imageDataUrl = await chrome.tabs.captureVisibleTab(undefined, {
    format: "png"
  });

  const response = await fetch(`${API_BASE}/describe-image`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: userQuery,
      image_data_url: imageDataUrl
    })
  });

  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(data.error || `Request failed with status ${response.status}`);
  }
  return data.reply || "I have no description.";
}
