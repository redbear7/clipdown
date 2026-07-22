const listEl = document.getElementById("media-list");
const countEl = document.getElementById("count");
const dlBtn = document.getElementById("download-btn");
const selectAllEl = document.getElementById("select-all");
const statusBar = document.getElementById("status-bar");
const statusText = document.getElementById("status-text");

// Log panel elements
const logPanel = document.getElementById("log-panel");
const logBody = document.getElementById("log-body");
const logContent = document.getElementById("log-content");
const logPhase = document.getElementById("log-phase");
const logProgressWrap = document.getElementById("log-progress-wrap");
const logProgressFill = document.getElementById("log-progress-fill");
const logProgressText = document.getElementById("log-progress-text");
const logCopyBtn = document.getElementById("log-copy");
const serverBtn = document.getElementById("server-status");
const serverLabel = serverBtn.querySelector(".server-label");

let items = [];
let globalFormat = "video";
let autoMp3 = false;
const DEFAULT_HEIGHT = "1920"; // 1920p default

// ── Runtime timer (popup open → file saved) ──
const T_START = performance.now();
let runtimePhase = "init"; // init | request | processing | saving | complete
let runtimeFinishedAt = null;
let runtimeTimer = null;

const PHASE_LABEL = {
  init: "⏱ initializing...",
  request: "📡 sending request...",
  processing: "⚙ server processing...",
  saving: "💾 saving file...",
  complete: "✓ Complete",
};

function getElapsedMs() {
  return Math.round(performance.now() - T_START);
}

function formatElapsed(ms) {
  return (ms / 1000).toFixed(1) + "s";
}

function updateRuntimeBadge() {
  const el = document.getElementById("runtime-badge");
  const statusEl = document.getElementById("runtime-status");
  if (!el) return;

  const ms = runtimeFinishedAt !== null ? runtimeFinishedAt : getElapsedMs();
  el.textContent = formatElapsed(ms);

  if (runtimeFinishedAt !== null) {
    el.classList.add("done");
    if (statusEl) {
      statusEl.textContent = PHASE_LABEL.complete;
      statusEl.classList.add("done");
    }
    if (runtimeTimer) {
      clearInterval(runtimeTimer);
      runtimeTimer = null;
    }
  } else {
    if (statusEl) statusEl.textContent = PHASE_LABEL[runtimePhase] || PHASE_LABEL.init;
  }
}

runtimeTimer = setInterval(updateRuntimeBadge, 100);

function setRuntimePhase(phase) {
  runtimePhase = phase;
  updateRuntimeBadge();
}

function markRuntimeComplete() {
  if (runtimeFinishedAt === null) {
    runtimeFinishedAt = getElapsedMs();
    updateRuntimeBadge();
  }
}

// Listen to chrome.downloads to detect actual file save
if (chrome.downloads?.onChanged) {
  chrome.downloads.onChanged.addListener((delta) => {
    if (delta.state?.current === "complete") {
      markRuntimeComplete();
    }
  });
}

// Load saved settings
async function loadSettings() {
  try {
    const stored = await chrome.storage.local.get(["autoMp3", "forceH264", "fastMode"]);
    autoMp3 = !!stored.autoMp3;
    document.getElementById("auto-mp3").checked = autoMp3;
    document.getElementById("force-h264").checked = !!stored.forceH264;
    document.getElementById("fast-mode").checked = !!stored.fastMode;
    if (autoMp3) {
      document.querySelector(".fmt-btn.active")?.classList.remove("active");
      document.querySelector('.fmt-btn[data-fmt="audio"]').classList.add("active");
      globalFormat = "audio";
    }
  } catch (e) {
    console.warn("Settings load failed:", e);
  }
}

// Save settings on change
document.getElementById("auto-mp3").addEventListener("change", (e) => {
  autoMp3 = e.target.checked;
  chrome.storage.local.set({ autoMp3 });
});
document.getElementById("force-h264").addEventListener("change", (e) => {
  chrome.storage.local.set({ forceH264: e.target.checked });
});
document.getElementById("fast-mode").addEventListener("change", (e) => {
  chrome.storage.local.set({ fastMode: e.target.checked });
});

// Format toggle
document.querySelectorAll(".fmt-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (globalFormat === btn.dataset.fmt) return;
    document.querySelector(".fmt-btn.active").classList.remove("active");
    btn.classList.add("active");
    globalFormat = btn.dataset.fmt;
    // Reset finished states so user can re-download in new format
    items.forEach((it) => {
      if (it.dlStatus === "done" || it.dlStatus === "error") {
        it.dlStatus = null;
        it.dlError = null;
        it.jobId = null;
        it.progress = 0;
      }
    });
    render();
  });
});

// Select all
selectAllEl.addEventListener("change", () => {
  items.forEach((it) => (it.checked = selectAllEl.checked));
  render();
});

// Server status check
async function checkServer(silent) {
  if (!silent) {
    serverBtn.classList.remove("online", "offline");
    serverBtn.classList.add("checking");
    serverLabel.textContent = "...";
  }
  try {
    const res = await fetch("http://localhost:8899/", {
      signal: AbortSignal.timeout(2000),
    });
    if (res.ok) {
      serverBtn.classList.remove("checking", "offline");
      serverBtn.classList.add("online");
      serverLabel.textContent = "Online";
      serverBtn.title = "ClipDown server is running. Click to restart.";
      return true;
    }
    throw new Error("bad status");
  } catch {
    serverBtn.classList.remove("checking", "online");
    serverBtn.classList.add("offline");
    serverLabel.textContent = "Offline";
    serverBtn.title = "Click to start the server";
    return false;
  }
}

async function startServerViaNative() {
  serverBtn.classList.remove("online", "offline");
  serverBtn.classList.add("checking");
  serverLabel.textContent = "Starting...";
  try {
    const res = await chrome.runtime.sendMessage({
      action: "serverControl",
      command: "start",
    });
    if (!res?.ok) {
      serverLabel.textContent = "Install host";
      serverBtn.title = `Native host not installed.\nRun extension/native/install.sh\nError: ${res?.error || "unknown"}`;
      serverBtn.classList.remove("checking");
      serverBtn.classList.add("offline");
      return false;
    }
    // Wait for server to come up
    for (let i = 0; i < 10; i++) {
      await new Promise((r) => setTimeout(r, 500));
      if (await checkServer(true)) return true;
    }
    return false;
  } catch (err) {
    serverBtn.classList.remove("checking", "online");
    serverBtn.classList.add("offline");
    serverLabel.textContent = "Error";
    serverBtn.title = `Failed: ${err.message}`;
    return false;
  }
}

serverBtn.addEventListener("click", async () => {
  // If offline, try to start; if online, just recheck
  const isOnline = serverBtn.classList.contains("online");
  if (isOnline) {
    await checkServer();
  } else {
    await startServerViaNative();
  }
});

checkServer();

// Log copy
logCopyBtn.addEventListener("click", () => {
  const text = logContent.innerText;
  navigator.clipboard.writeText(text).then(() => {
    logCopyBtn.textContent = "Copied!";
    logCopyBtn.classList.add("copied");
    setTimeout(() => {
      logCopyBtn.textContent = "Copy";
      logCopyBtn.classList.remove("copied");
    }, 1500);
  });
});

// Download selected
dlBtn.addEventListener("click", downloadSelected);

// Init
loadSettings().then(init);

async function init() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    showEmpty("No active tab");
    return;
  }

  if (!tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://") || tab.url.startsWith("about:")) {
    showEmpty("Cannot access this page");
    return;
  }

  let res = null;

  try {
    res = await chrome.tabs.sendMessage(tab.id, { action: "getMediaUrls" });
  } catch {
    try {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["utils/media-patterns.js", "content/content-script.js"],
      });
      // Retry sending message — content script registers listener synchronously after load
      for (let i = 0; i < 5; i++) {
        try {
          res = await chrome.tabs.sendMessage(tab.id, { action: "getMediaUrls" });
          if (res) break;
        } catch {
          await new Promise((r) => setTimeout(r, 50));
        }
      }
      if (!res) {
        showEmpty("Cannot access this page");
        return;
      }
    } catch {
      showEmpty("Cannot access this page");
      return;
    }
  }

  if (!res?.urls?.length) {
    showEmpty("No downloadable media found");
    return;
  }

  items = res.urls.map((u) => ({
    ...u,
    info: null,
    checked: false,
    selectedFormat: null,
    jobId: null,
    dlStatus: null,
    dlError: null,
    logs: [],
    progress: 0,
    phase: "",
    speed: "",
  }));

  if (items.length > 0 && items[0].source === "PAGE") {
    items[0].checked = true;
  }

  // Restore active/recent jobs from service worker
  await restoreActiveJobs();

  render();
  fetchAllInfo();

  // Auto-download as MP3 if option enabled and no active jobs
  if (autoMp3 && items.length > 0) {
    const hasActiveOrDone = items.some((it) => it.dlStatus === "downloading" || it.dlStatus === "done");
    if (!hasActiveOrDone) {
      // Skip info fetch wait — backend extracts title via --write-info-json
      // This saves 3-8 seconds vs waiting for /api/info
      downloadSelected();
    }
  }
}

async function restoreActiveJobs() {
  try {
    // Race against timeout in case service worker is unresponsive
    const res = await Promise.race([
      chrome.runtime.sendMessage({ action: "getAllJobs" }),
      new Promise((resolve) => setTimeout(() => resolve(null), 1500)),
    ]);
    if (!res) return;
    if (!res?.jobs?.length) return;

    let hasActive = false;
    for (const job of res.jobs) {
      const idx = items.findIndex((it) => it.url === job.url);
      if (idx === -1) continue;

      const item = items[idx];
      item.jobId = job.jobId;
      item.dlStatus = job.status;
      item.dlError = job.error;
      item.logs = job.logs || [];
      item.progress = job.progress || 0;
      item.phase = job.phase || "";
      item.speed = job.speed || "";
      item.checked = true;

      if (job.status === "downloading") {
        hasActive = true;
        // Resume polling for UI updates
        pollCardStatus(idx);
      }
    }

    // Show log panel if any active or recently finished jobs
    if (res.jobs.length > 0) {
      logPanel.classList.remove("hidden");
      logProgressWrap.classList.remove("hidden");

      // Rebuild log content from all jobs
      logContent.innerHTML = "";
      for (const job of res.jobs) {
        if (job.logs?.length) {
          appendLog(`── ${job.title || job.url} ──`);
          for (const l of job.logs) {
            const type = l.includes("Error") ? "error"
              : l.includes("Done") || l.includes("complete") ? "done"
              : l.includes("%") ? "progress"
              : "";
            appendLog(l, type);
          }
        }
      }

      // Update progress for the last active job
      const activeJob = res.jobs.find((j) => j.status === "downloading");
      if (activeJob) {
        updateLogProgress(activeJob.progress || 0, activeJob.phase, activeJob.speed);
        showStatus(`Download in progress...`);
        dlBtn.disabled = true;
      } else {
        const allDone = res.jobs.every((j) => j.status === "done");
        if (allDone && res.jobs.length > 0) {
          updateLogProgress(100, "done", "");
          showStatus(`${res.jobs.length} download(s) complete`);
        }
      }
    }
  } catch (e) {
    console.warn("Could not restore jobs:", e);
  }
}

function showEmpty(msg) {
  listEl.innerHTML = `<div class="empty-state"><p>${msg}</p></div>`;
  updateCount();
}

function showStatus(msg, isError) {
  statusBar.classList.remove("hidden", "error");
  if (isError) statusBar.classList.add("error");
  statusText.textContent = msg;
}

// Fetch info for all items (max 3 concurrent)
async function fetchAllInfo() {
  const queue = items.map((_, i) => i);
  const concurrency = 3;
  let active = 0;

  function next() {
    while (active < concurrency && queue.length > 0) {
      const idx = queue.shift();
      active++;
      fetchItemInfo(idx).finally(() => {
        active--;
        next();
      });
    }
  }
  next();
}

async function fetchItemInfo(idx) {
  const item = items[idx];

  // Try session cache first (instant)
  const cacheKey = `info:${item.url}`;
  try {
    const cached = sessionStorage.getItem(cacheKey);
    if (cached) {
      const parsed = JSON.parse(cached);
      if (Date.now() - parsed._ts < 600000) {
        item.info = parsed.data;
        if (parsed.data.formats?.length) {
          const preferred = parsed.data.formats.find((f) => f.id === DEFAULT_HEIGHT);
          item.selectedFormat = preferred ? preferred.id : parsed.data.formats[0].id;
        }
        renderCard(idx);
        return;
      }
    }
  } catch {}

  try {
    // Direct fetch — bypass service worker for simplicity & reliability
    const res = await fetch("http://localhost:8899/api/info", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: item.url }),
      signal: AbortSignal.timeout(60000),
    });
    const data = await res.json();

    if (data.error) {
      item.info = { error: data.error };
    } else {
      item.info = data;
      if (data.formats?.length) {
        const preferred = data.formats.find((f) => f.id === DEFAULT_HEIGHT);
        item.selectedFormat = preferred ? preferred.id : data.formats[0].id;
      }
      try {
        sessionStorage.setItem(cacheKey, JSON.stringify({ _ts: Date.now(), data }));
      } catch {}
    }
  } catch (err) {
    console.error("fetchItemInfo failed:", err);
    item.info = { error: err.name === "TimeoutError" ? "Timed out" : "Server not reachable" };
  }
  renderCard(idx);
}

function render() {
  if (items.length === 0) {
    showEmpty("No downloadable media found");
    return;
  }

  listEl.innerHTML = "";
  items.forEach((_, i) => {
    const el = buildCard(i);
    listEl.appendChild(el);
  });
  updateCount();
}

function renderCard(idx) {
  const cards = listEl.querySelectorAll(".media-card");
  if (cards[idx]) {
    const newCard = buildCard(idx);
    cards[idx].replaceWith(newCard);
  }
  updateCount();
}

function buildCard(idx) {
  const item = items[idx];
  const card = document.createElement("div");
  card.className = "media-card";
  if (item.dlStatus) card.classList.add(item.dlStatus);

  const cb = document.createElement("input");
  cb.type = "checkbox";
  cb.checked = item.checked;
  cb.addEventListener("change", () => {
    item.checked = cb.checked;
    updateCount();
  });

  const body = document.createElement("div");
  body.className = "card-body";

  const top = document.createElement("div");
  top.className = "card-top";

  const badge = document.createElement("span");
  badge.className = "source-badge";
  badge.textContent = item.source;
  top.appendChild(badge);

  if (item.info && !item.info.error) {
    const title = document.createElement("span");
    title.className = "card-title";
    title.textContent = item.info.title || item.label;
    title.title = item.info.title || item.label;
    top.appendChild(title);
  } else if (item.info?.error) {
    const title = document.createElement("span");
    title.className = "card-title";
    title.textContent = item.label;
    title.title = item.url;
    top.appendChild(title);
  } else {
    const sk = document.createElement("div");
    sk.className = "skeleton skeleton-title";
    top.appendChild(sk);
  }

  body.appendChild(top);

  const urlEl = document.createElement("div");
  urlEl.className = "card-url";
  urlEl.textContent = item.url;
  urlEl.title = item.url;
  body.appendChild(urlEl);

  if (item.info && !item.info.error) {
    const meta = document.createElement("div");
    meta.className = "card-meta";

    if (item.info.thumbnail) {
      const thumb = document.createElement("img");
      thumb.className = "card-thumb";
      thumb.src = item.info.thumbnail;
      thumb.alt = "";
      meta.appendChild(thumb);
    }

    const infoSpan = document.createElement("span");
    infoSpan.className = "card-info";
    const parts = [];
    if (item.info.uploader) parts.push(item.info.uploader);
    if (item.info.duration) parts.push(formatDuration(item.info.duration));
    infoSpan.textContent = parts.join(" · ");
    if (parts.length) meta.appendChild(infoSpan);

    body.appendChild(meta);

    if (item.info.formats?.length && globalFormat === "video") {
      const chips = document.createElement("div");
      chips.className = "quality-chips";
      for (const fmt of item.info.formats) {
        const chip = document.createElement("button");
        chip.className = "q-chip";
        if (item.selectedFormat === fmt.id) chip.classList.add("active");
        if (fmt.id === DEFAULT_HEIGHT) chip.classList.add("recommended");
        chip.textContent = fmt.id === DEFAULT_HEIGHT ? `${fmt.label} ★` : fmt.label;
        chip.addEventListener("click", () => {
          item.selectedFormat = fmt.id;
          renderCard(idx);
        });
        chips.appendChild(chip);
      }
      body.appendChild(chips);
    }
  } else if (item.info?.error) {
    const err = document.createElement("div");
    err.className = "card-info";
    err.style.color = "var(--error)";
    err.textContent = item.info.error;
    body.appendChild(err);
  }

  const statusEl = document.createElement("div");
  statusEl.className = "card-status";
  if (item.dlStatus === "downloading") {
    statusEl.innerHTML = '<div class="spinner"></div>';
  } else if (item.dlStatus === "done") {
    statusEl.innerHTML = '<span class="icon-done">✓</span>';
  } else if (item.dlStatus === "error") {
    statusEl.innerHTML = '<span class="icon-error">✕</span>';
  }

  card.appendChild(cb);
  card.appendChild(body);
  card.appendChild(statusEl);

  return card;
}

function updateCount() {
  const checked = items.filter((it) => it.checked).length;
  const total = items.length;
  countEl.textContent = `${checked}/${total} selected`;
  dlBtn.disabled = checked === 0;
  selectAllEl.checked = total > 0 && checked === total;
}

async function downloadSelected() {
  setRuntimePhase("request");
  const toDownload = items
    .map((it, i) => ({ it, i }))
    .filter(({ it }) => it.checked && it.dlStatus !== "downloading");

  if (toDownload.length === 0) return;

  dlBtn.disabled = true;

  // Show log panel
  logPanel.classList.remove("hidden");
  logProgressWrap.classList.remove("hidden");
  logContent.innerHTML = "";
  appendLog(`Starting ${toDownload.length} download(s)...`);
  scrollLogToBottom();

  showStatus(`Downloading ${toDownload.length} item(s)...`);

  for (const { it, i } of toDownload) {
    it.dlStatus = "downloading";
    it.logs = [];
    it.progress = 0;
    renderCard(i);

    appendLog(`\n── ${it.info?.title || it.url} ──`);

    try {
      const forceH264 = document.getElementById("force-h264")?.checked || false;
      const fastMode = document.getElementById("fast-mode")?.checked || false;
      const res = await chrome.runtime.sendMessage({
        action: "startDownload",
        url: it.url,
        format: globalFormat,
        format_id: globalFormat === "video" ? it.selectedFormat : null,
        title: it.info?.title || "",
        force_h264: forceH264,
        fast_mode: fastMode,
      });

      if (res.error) {
        it.dlStatus = "error";
        it.dlError = res.error;
        appendLog(`Error: ${res.error}`, "error");
      } else {
        it.jobId = res.jobId;
        setRuntimePhase("processing");
        pollCardStatus(i);
      }
    } catch {
      it.dlStatus = "error";
      it.dlError = "Server not reachable";
      appendLog("Error: Server not reachable", "error");
    }
    renderCard(i);
  }
}

async function pollCardStatus(idx) {
  const item = items[idx];
  if (!item.jobId || item.dlStatus === "done" || item.dlStatus === "error") return;

  try {
    const res = await chrome.runtime.sendMessage({
      action: "getJobStatus",
      jobId: item.jobId,
    });

    if (res.logs && res.logs.length > item.logs.length) {
      const newLogs = res.logs.slice(item.logs.length);
      item.logs = res.logs;
      for (const l of newLogs) {
        const type = l.includes("Error") ? "error"
          : l.includes("Done") || l.includes("complete") ? "done"
          : l.includes("%") ? "progress"
          : "";
        appendLog(l, type);
      }
    }

    // Update progress bar
    if (res.progress !== undefined) {
      item.progress = res.progress;
      updateLogProgress(res.progress, res.phase, res.speed);
    }

    if (res.status === "done") {
      item.dlStatus = "done";
      renderCard(idx);
      checkAllDone();
      return;
    }
    if (res.status === "error") {
      item.dlStatus = "error";
      item.dlError = res.error;
      renderCard(idx);
      checkAllDone();
      return;
    }
  } catch {}

  setTimeout(() => pollCardStatus(idx), 800);
}

function checkAllDone() {
  const downloading = items.some((it) => it.dlStatus === "downloading");
  if (!downloading) {
    const doneCount = items.filter((it) => it.dlStatus === "done").length;
    const errCount = items.filter((it) => it.dlStatus === "error").length;
    if (errCount > 0) {
      showStatus(`Done: ${doneCount} succeeded, ${errCount} failed`, true);
    } else {
      showStatus(`All ${doneCount} downloads complete!`);
    }
    dlBtn.disabled = false;
    updateLogProgress(100, "done", "");
  }
}

// Listen for background updates
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.action === "downloadUpdate") {
    const item = items.find((it) => it.jobId === msg.jobId);
    if (!item) return;

    if (msg.logs && msg.logs.length > item.logs.length) {
      const newLogs = msg.logs.slice(item.logs.length);
      item.logs = msg.logs;
      for (const l of newLogs) {
        const type = l.includes("Error") ? "error"
          : l.includes("Done") || l.includes("complete") ? "done"
          : l.includes("%") ? "progress"
          : "";
        appendLog(l, type);
      }
    }

    if (msg.progress !== undefined) {
      item.progress = msg.progress;
      updateLogProgress(msg.progress, msg.phase, msg.speed);
    }

    if (msg.status === "done" || msg.status === "error") {
      item.dlStatus = msg.status;
      if (msg.error) item.dlError = msg.error;
      const idx = items.indexOf(item);
      renderCard(idx);
      checkAllDone();
    }
  }
});

// ── Log panel helpers ──

function appendLog(text, type) {
  const line = document.createElement("div");
  line.className = "log-line";
  if (type) line.classList.add(`log-${type}`);
  line.textContent = text;
  logContent.appendChild(line);
  scrollLogToBottom();
}

function scrollLogToBottom() {
  logBody.scrollTop = logBody.scrollHeight;
}

function updateLogProgress(pct, phase, speed) {
  logProgressFill.style.width = `${pct}%`;
  const parts = [`${Math.round(pct)}%`];
  if (speed) parts.push(speed);
  logProgressText.textContent = parts.join(" · ");

  const phaseLabels = {
    queued: "Queued",
    starting: "Starting",
    downloading: "Downloading",
    merging: "Merging",
    "re-encoding": "Re-encoding",
    done: "Complete",
  };
  logPhase.textContent = phaseLabels[phase] || phase || "";
}

function formatDuration(sec) {
  if (!sec) return "";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}
