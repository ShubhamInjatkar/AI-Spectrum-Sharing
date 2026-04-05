const els = {
  deviceIdInput: document.getElementById("deviceIdInput"),
  intervalSelect: document.getElementById("intervalSelect"),
  probeSizeSelect: document.getElementById("probeSizeSelect"),
  startButton: document.getElementById("startButton"),
  stopButton: document.getElementById("stopButton"),
  statusChip: document.getElementById("statusChip"),
  latencyValue: document.getElementById("latencyValue"),
  throughputValue: document.getElementById("throughputValue"),
  samplesValue: document.getElementById("samplesValue"),
  connectedDevicesValue: document.getElementById("connectedDevicesValue"),
  loadValue: document.getElementById("loadValue"),
  interferenceValue: document.getElementById("interferenceValue"),
  noiseValue: document.getElementById("noiseValue"),
  decisionValue: document.getElementById("decisionValue"),
  confidenceValue: document.getElementById("confidenceValue"),
  logList: document.getElementById("logList"),
};

const state = {
  running: false,
  timer: null,
  samplesSent: 0,
  deviceId: "",
};

function formatNumber(value, digits = 1) {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric.toFixed(digits) : "0.0";
}

function buildDefaultDeviceId() {
  const stored = localStorage.getItem("spectrum-device-id");
  if (stored) {
    return stored;
  }

  const platform = navigator.userAgentData?.platform || navigator.platform || "device";
  const safePlatform = platform.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
  const randomSuffix = Math.random().toString(36).slice(2, 8).toUpperCase();
  return `${safePlatform || "device"}-${randomSuffix}`;
}

function setStatus(label, variant) {
  els.statusChip.textContent = label;
  els.statusChip.className = `status-chip ${variant}`;
}

function appendLog(message) {
  const item = document.createElement("article");
  item.className = "log-item";
  item.innerHTML = `<span class="log-time">${new Date().toLocaleTimeString()}</span><p>${message}</p>`;
  els.logList.prepend(item);

  while (els.logList.children.length > 8) {
    els.logList.removeChild(els.logList.lastElementChild);
  }
}

function average(values) {
  if (!values.length) {
    return 0;
  }

  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function computeJitter(latencies) {
  if (latencies.length <= 1) {
    return 0;
  }

  const deltas = [];
  for (let index = 1; index < latencies.length; index += 1) {
    deltas.push(Math.abs(latencies[index] - latencies[index - 1]));
  }
  return average(deltas);
}

async function fetchProbe(sizeKb) {
  const startedAt = performance.now();
  const response = await fetch(`/api/network/probe?size_kb=${sizeKb}&ts=${Date.now()}`, {
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Probe failed with ${response.status}`);
  }

  const buffer = await response.arrayBuffer();
  const durationMs = performance.now() - startedAt;
  return {
    latencyMs: durationMs,
    throughputMbps: (buffer.byteLength * 8) / Math.max(durationMs, 1) / 1000,
  };
}

async function collectLatencyWindow(attempts = 4) {
  const latencySamples = [];

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const probe = await fetchProbe(4);
      latencySamples.push(probe.latencyMs);
    } catch {}

    if (attempt < attempts - 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 120));
    }
  }

  if (!latencySamples.length) {
    throw new Error("All latency probes failed.");
  }

  return {
    latencyMs: average(latencySamples),
    jitterMs: computeJitter(latencySamples),
  };
}

async function publishTelemetry() {
  const probeSize = Number(els.probeSizeSelect.value);
  const deviceId = els.deviceIdInput.value.trim();

  if (!deviceId) {
    throw new Error("Device ID is required.");
  }

  const latencyWindow = await collectLatencyWindow(4);
  let throughputMbps = null;

  try {
    const throughputProbe = await fetchProbe(probeSize);
    throughputMbps = throughputProbe.throughputMbps;
  } catch {
    const fallback = navigator.connection?.downlink;
    throughputMbps = typeof fallback === "number" ? fallback : null;
  }

  const payload = {
    device_id: deviceId,
    latency_ms: Number(latencyWindow.latencyMs.toFixed(2)),
    throughput_mbps: throughputMbps == null ? null : Number(throughputMbps.toFixed(2)),
    jitter_ms: Number(latencyWindow.jitterMs.toFixed(2)),
  };

  const response = await fetch("/api/network/devices", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Telemetry upload failed with ${response.status}`);
  }

  const snapshot = await response.json();
  state.samplesSent += 1;
  els.latencyValue.textContent = `${formatNumber(payload.latency_ms)} ms`;
  els.throughputValue.textContent =
    payload.throughput_mbps == null ? "n/a" : `${formatNumber(payload.throughput_mbps)} Mbps`;
  els.samplesValue.textContent = String(state.samplesSent);
  els.connectedDevicesValue.textContent = String(snapshot.metrics.device_count ?? 0);
  els.loadValue.textContent = `${formatNumber(snapshot.metrics.load)}%`;
  els.interferenceValue.textContent = `${formatNumber(snapshot.metrics.interference)}%`;
  els.noiseValue.textContent = `${formatNumber(snapshot.metrics.noise)}%`;
  els.decisionValue.textContent = snapshot.agent.anchor_device || "Waiting";
  els.confidenceValue.textContent = `${formatNumber(snapshot.agent.confidence)}%`;
  appendLog(
    `Sent telemetry for ${payload.device_id}: ${formatNumber(payload.latency_ms)} ms latency, ${formatNumber(
      payload.jitter_ms,
    )} ms jitter, ${
      payload.throughput_mbps == null ? "throughput unavailable" : `${formatNumber(payload.throughput_mbps)} Mbps`
    }.`
  );
}

async function tick() {
  if (!state.running) {
    return;
  }

  try {
    setStatus("Streaming", "status-live");
    await publishTelemetry();
  } catch (error) {
    setStatus("Error", "status-error");
    appendLog(error.message || "Unable to publish telemetry.");
  } finally {
    if (state.running) {
      state.timer = window.setTimeout(tick, Number(els.intervalSelect.value));
    }
  }
}

function start() {
  if (state.running) {
    return;
  }

  state.deviceId = els.deviceIdInput.value.trim() || buildDefaultDeviceId();
  els.deviceIdInput.value = state.deviceId;
  localStorage.setItem("spectrum-device-id", state.deviceId);
  state.running = true;
  els.startButton.disabled = true;
  els.stopButton.disabled = false;
  appendLog(`Starting telemetry stream for ${state.deviceId}.`);
  void tick();
}

function stop() {
  state.running = false;
  window.clearTimeout(state.timer);
  state.timer = null;
  els.startButton.disabled = false;
  els.stopButton.disabled = true;
  setStatus("Idle", "status-idle");
  appendLog("Telemetry stream paused.");
}

function init() {
  const defaultId = buildDefaultDeviceId();
  els.deviceIdInput.value = defaultId;
  state.deviceId = defaultId;
  setStatus("Idle", "status-idle");
  appendLog("Collector ready. Press Start Streaming to publish live telemetry.");
  els.startButton.addEventListener("click", start);
  els.stopButton.addEventListener("click", stop);
}

init();
