const API_BASE = "http://localhost:8899";
const NATIVE_HOST = "com.reclip.server";
const activeJobs = new Map();
const pollingJobs = new Set(); // prevent duplicate polling

function nativeMessage(action) {
  return new Promise((resolve, reject) => {
    try {
      chrome.runtime.sendNativeMessage(NATIVE_HOST, { action }, (response) => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else {
          resolve(response);
        }
      });
    } catch (e) {
      reject(e);
    }
  });
}

function datePrefix() {
  const now = new Date();
  const yy = String(now.getFullYear()).slice(-2);
  const mm = String(now.getMonth() + 1).padStart(2, "0");
  const dd = String(now.getDate()).padStart(2, "0");
  return `${yy}${mm}${dd}`;
}

function sanitizeFilename(name) {
  return name.replace(/[<>:"/\\|?*]/g, "").replace(/\s+/g, " ").trim();
}

async function fetchInfo(url) {
  const res = await fetch(`${API_BASE}/api/info`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return res.json();
}

async function startDownload({ url, format, format_id, title, force_h264, fast_mode, tabId }) {
  const res = await fetch(`${API_BASE}/api/download`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, format, format_id, title, force_h264, fast_mode }),
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error);

  const jobId = data.job_id;
  activeJobs.set(jobId, {
    jobId,
    url, // store URL for popup restoration
    title,
    format,
    formatId: format_id,
    status: "downloading",
    tabId,
    progress: 0,
    phase: "queued",
    speed: "",
    logs: [],
    lastLog: 0,
    downloaded: false,
    startedAt: Date.now(),
  });

  schedulePoll(jobId);
  return jobId;
}

function pruneOldJobs() {
  // Remove finished jobs older than 5 minutes
  const cutoff = Date.now() - 5 * 60 * 1000;
  for (const [id, job] of activeJobs) {
    if ((job.status === "done" || job.status === "error") &&
        (job.finishedAt || 0) < cutoff) {
      activeJobs.delete(id);
    }
  }
}

function schedulePoll(jobId) {
  if (pollingJobs.has(jobId)) return; // already polling
  pollingJobs.add(jobId);
  pollJob(jobId);
}

async function pollJob(jobId) {
  const job = activeJobs.get(jobId);
  if (!job || job.status !== "downloading") {
    pollingJobs.delete(jobId);
    return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/status/${jobId}?last_log=${job.lastLog}`);
    const data = await res.json();

    // Append new logs
    if (data.logs?.length) {
      job.logs.push(...data.logs);
      job.lastLog = data.total_logs;
    }

    job.progress = data.progress || 0;
    job.phase = data.phase || "";
    job.speed = data.speed || "";

    if (data.status === "done" && !job.downloaded) {
      job.downloaded = true;
      const rawFilename = `${datePrefix()}_${data.filename}`;
      const filename = sanitizeFilename(rawFilename);
      Object.assign(job, { status: "done", filename, finishedAt: Date.now() });

      // Use token in URL if provided (avoids auth header for chrome.downloads)
      const tokenQuery = data.download_token ? `?token=${encodeURIComponent(data.download_token)}` : "";
      chrome.downloads.download({
        url: `${API_BASE}/api/file/${jobId}${tokenQuery}`,
        filename,
        saveAs: false,
      });

      broadcastUpdate(jobId);
      pollingJobs.delete(jobId);
      return;
    }

    if (data.status === "error") {
      Object.assign(job, { status: "error", error: data.error, finishedAt: Date.now() });
      broadcastUpdate(jobId);
      pollingJobs.delete(jobId);
      return;
    }

    // Still in progress
    broadcastUpdate(jobId);
    setTimeout(() => pollJob(jobId), 800);
  } catch (err) {
    setTimeout(() => pollJob(jobId), 2000);
  }
}

function broadcastUpdate(jobId) {
  const job = activeJobs.get(jobId);
  if (!job) return;
  chrome.runtime.sendMessage({
    action: "downloadUpdate",
    jobId,
    status: job.status,
    filename: job.filename,
    error: job.error,
    progress: job.progress,
    phase: job.phase,
    speed: job.speed,
    logs: job.logs,
  }).catch(() => {});
}

// Keep service worker alive — only wake up polls that stopped
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "reclip-keepalive") {
    let hasActive = false;
    for (const [jobId, job] of activeJobs) {
      if (job.status === "downloading") {
        hasActive = true;
        schedulePoll(jobId); // safe: won't double-poll
      }
    }
    if (!hasActive) {
      chrome.alarms.clear("reclip-keepalive");
    }
  }
});

// Message handler
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.action === "fetchInfo") {
    fetchInfo(msg.url)
      .then((data) => sendResponse(data))
      .catch((err) => sendResponse({ error: err.message }));
    return true;
  }

  if (msg.action === "startDownload") {
    chrome.alarms.create("reclip-keepalive", { periodInMinutes: 0.4 });
    startDownload(msg)
      .then((jobId) => sendResponse({ jobId }))
      .catch((err) => sendResponse({ error: err.message }));
    return true;
  }

  if (msg.action === "getJobStatus") {
    const job = activeJobs.get(msg.jobId);
    sendResponse(job || { status: "unknown" });
    return true;
  }

  if (msg.action === "getAllJobs") {
    pruneOldJobs();
    sendResponse({ jobs: Array.from(activeJobs.values()) });
    return true;
  }

  if (msg.action === "serverControl") {
    nativeMessage(msg.command)
      .then((res) => sendResponse({ ok: true, ...res }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
});
