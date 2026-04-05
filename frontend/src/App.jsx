import { useEffect, useMemo, useRef, useState } from "react";
import { Chart, registerables } from "chart.js";

Chart.register(...registerables);

const DEFAULT_CONTROLS = {
  users: 8,
  noiseLevel: 35,
  bandwidth: 50,
};
const APP_MODES = [
  { key: "simulation", label: "Simulation Mode" },
  { key: "live-network", label: "Live Network Mode" },
];

const PRESETS = [
  { label: "Congested", hint: "high load", users: 18, noiseLevel: 72, bandwidth: 30 },
  { label: "Balanced", hint: "steady mix", users: 10, noiseLevel: 40, bandwidth: 55 },
  { label: "Clean", hint: "low noise", users: 6, noiseLevel: 24, bandwidth: 80 },
];

const CHANNEL_COLORS = ["#63f3ff", "#bc7cff", "#22d3ee", "#f472b6", "#f59e0b"];
const STATUS_STYLES = {
  Normal: {
    dot: "bg-emerald-400",
    chip: "border-emerald-400/20 bg-emerald-500/10 text-emerald-100",
    panel: "border-emerald-400/16 bg-emerald-500/[0.06]",
  },
  Warning: {
    dot: "bg-amber-400",
    chip: "border-amber-400/20 bg-amber-500/10 text-amber-100",
    panel: "border-amber-400/16 bg-amber-500/[0.06]",
  },
  Critical: {
    dot: "bg-rose-400",
    chip: "border-rose-400/20 bg-rose-500/10 text-rose-100",
    panel: "border-rose-400/16 bg-rose-500/[0.06]",
  },
};

function formatNumber(value, digits = 1) {
  return Number(value ?? 0).toFixed(digits);
}

function clampNumber(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function makeSeed() {
  return Math.floor(100000 + Math.random() * 800000);
}

function buildSocketUrl(path = "/ws/live") {
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  return `${protocol}://${window.location.host}${path}`;
}

function toRequestPayload(nextControls, seed, tick = 0) {
  return {
    users: nextControls.users,
    noise_level: nextControls.noiseLevel,
    bandwidth: nextControls.bandwidth,
    seed,
    tick,
  };
}

function getChannelStatus(channel, optimized) {
  const interference = optimized ? channel.optimized_interference : channel.interference;
  const secondary = optimized ? channel.headroom : channel.quality;

  if (interference >= 70 || secondary <= 35) {
    return "Critical";
  }

  if (interference >= 45 || secondary <= 60) {
    return "Warning";
  }

  return "Normal";
}

function getSystemStatus(simulation, decision) {
  if (!simulation) {
    return "Normal";
  }

  const interference = decision?.metrics.optimized_interference ?? simulation.metrics.interference;
  const efficiency = decision?.metrics.efficiency ?? simulation.metrics.efficiency;

  if (interference >= 65 || efficiency <= 55) {
    return "Critical";
  }

  if (interference >= 40 || efficiency <= 72) {
    return "Warning";
  }

  return "Normal";
}

function getLiveNetworkStatus(networkFrame) {
  if (!networkFrame) {
    return "Normal";
  }

  if (networkFrame.event?.level) {
    return networkFrame.event.level;
  }

  const metrics = networkFrame.metrics;
  if (metrics.interference >= 48 || metrics.noise >= 36 || metrics.avg_packet_loss >= 4) {
    return "Critical";
  }

  if (metrics.interference >= 28 || metrics.noise >= 18 || metrics.avg_packet_loss >= 1.5) {
    return "Warning";
  }

  return "Normal";
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function postJSON(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return response.json();
}

function Slider({ label, value, min, max, step, unit, onChange }) {
  return (
    <label className="block rounded-2xl border border-white/[0.08] bg-white/[0.03] px-3.5 py-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <p className="text-sm font-medium text-slate-200">{label}</p>
          <p className="mt-1 text-[10px] uppercase tracking-[0.18em] text-slate-500">
            {min}
            {unit} - {max}
            {unit}
          </p>
        </div>
        <div className="shrink-0 self-start rounded-full border border-violet-400/20 bg-violet-500/10 px-3 py-1 text-sm font-semibold text-violet-100 sm:self-auto">
          {value}
          {unit}
        </div>
      </div>
      <input
        className="mt-3"
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(event) => onChange(Number(event.target.value))}
      />
    </label>
  );
}

function PanelHeader({ eyebrow, title, meta }) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <p className="text-[11px] uppercase tracking-[0.24em] text-cyan-300/70">{eyebrow}</p>
        <h2 className="mt-2 text-xl font-semibold text-white">{title}</h2>
      </div>
      {meta ? <div className="shrink-0 self-start sm:self-auto">{meta}</div> : null}
    </div>
  );
}

function StatusIndicator({ status, compact = false }) {
  const style = STATUS_STYLES[status];

  return (
    <div
      className={`inline-flex items-center rounded-full border font-medium ${style.chip} ${
        compact ? "gap-1.5 px-2.5 py-1 text-xs" : "gap-2 px-3 py-1 text-sm"
      }`}
    >
      <span className={`${compact ? "h-2 w-2" : "h-2.5 w-2.5"} rounded-full ${style.dot}`} />
      {status}
    </div>
  );
}

function MetricCard({ label, value, helper, accent }) {
  const valueClass =
    accent === "cyan" ? "text-cyan-100" : accent === "pink" ? "text-pink-100" : "text-violet-100";

  return (
    <div className="glass-panel rounded-3xl p-4">
      <p className="text-xs uppercase tracking-[0.24em] text-slate-500">{label}</p>
      <div className={`mt-3 text-2xl font-semibold tracking-tight sm:text-3xl ${valueClass}`}>{value}</div>
      <p className="mt-3 text-sm leading-6 text-slate-400">{helper}</p>
    </div>
  );
}

function DialGauge({ label, value, color, compact = false }) {
  const safeValue = clampNumber(Number(value ?? 0), 0, 100);
  const size = compact ? 92 : 104;
  const center = size / 2;
  const radius = compact ? 33 : 40;
  const strokeWidth = compact ? 11 : 12;
  const circumference = 2 * Math.PI * radius;
  const strokeOffset = circumference * (1 - safeValue / 100);

  return (
    <div className={`rounded-3xl border border-white/[0.08] bg-white/[0.03] ${compact ? "p-2.5" : "p-3"}`}>
      <div className="mx-auto relative" style={{ height: `${size}px`, width: `${size}px` }}>
        <svg className="-rotate-90" viewBox={`0 0 ${size} ${size}`} aria-hidden="true" style={{ height: size, width: size }}>
          <circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke="rgba(148, 163, 184, 0.16)"
            strokeWidth={strokeWidth}
          />
          <circle
            cx={center}
            cy={center}
            r={radius}
            fill="none"
            stroke={color}
            strokeLinecap="round"
            strokeWidth={strokeWidth}
            strokeDasharray={circumference}
            strokeDashoffset={strokeOffset}
          />
        </svg>
        <div className={`absolute rounded-full border border-white/[0.06] bg-slate-950/92 ${compact ? "inset-[9px]" : "inset-[10px]"}`} />
        <div className="absolute inset-0 grid place-items-center text-center">
          <div className="flex flex-col items-center justify-center leading-none">
            <p className={`${compact ? "text-base" : "text-lg"} font-semibold text-white`}>{formatNumber(safeValue)}%</p>
            <p className={`mt-1 uppercase text-slate-500 ${compact ? "text-[9px] tracking-[0.16em]" : "text-[10px] tracking-[0.18em]"}`}>
              {label}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatTile({ label, value, accent = "text-white", compact = false }) {
  return (
    <div className={`rounded-2xl border border-white/[0.08] bg-white/[0.03] ${compact ? "px-2.5 py-2.5" : "px-3 py-3"}`}>
      <p className={`${compact ? "text-[9px] tracking-[0.14em]" : "text-[10px] tracking-[0.12em]"} uppercase text-slate-500`}>
        {label}
      </p>
      <p className={`font-semibold leading-tight break-words ${accent} ${compact ? "mt-1.5 text-base" : "mt-2 text-lg"}`}>{value}</p>
    </div>
  );
}

function ChannelCard({ channel, index, optimized, highlighted = false }) {
  const load = optimized ? channel.optimized_load : channel.load;
  const interference = optimized ? channel.optimized_interference : channel.interference;
  const note = optimized ? `${channel.assigned_users} assigned` : `${channel.user_count} users`;
  const secondaryLabel = optimized ? "Headroom" : "Quality";
  const secondaryValue = optimized ? `${formatNumber(channel.headroom)}%` : `${formatNumber(channel.quality)}%`;
  const label = optimized ? "AI Channel" : "Channel State";
  const status = getChannelStatus(channel, optimized);

  return (
    <div
      className={`glass-panel rounded-3xl p-3.5 sm:p-4 ${highlighted ? "channel-glow ring-1 ring-cyan-300/30" : ""} ${
        status === "Critical" ? "critical-pulse" : ""
      }`}
    >
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-start justify-between gap-2.5">
          <div className="min-w-0">
            <p className="text-[9px] uppercase tracking-[0.14em] text-slate-500">{label}</p>
            <h3 className="mt-1 text-[2rem] font-semibold leading-none text-white">{channel.id}</h3>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {highlighted ? (
              <div className="rounded-full border border-cyan-400/20 bg-cyan-500/10 px-2.5 py-1 text-[10px] leading-tight text-cyan-100">
                best route
              </div>
            ) : null}
            <div className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] leading-tight text-slate-300">
              {note}
            </div>
          </div>
        </div>

        <div className="grid gap-2.5">
          <div className="grid gap-2.5 lg:grid-cols-[92px_minmax(0,1fr)] lg:items-center">
            <DialGauge label="Load" value={load} color={CHANNEL_COLORS[index % CHANNEL_COLORS.length]} compact />
            <div className="grid gap-1.5">
              <div className="grid gap-2 sm:grid-cols-2">
                <StatTile label="Interference" value={`${formatNumber(interference)}%`} compact />
                <StatTile
                  label={secondaryLabel}
                  value={secondaryValue}
                  accent={optimized ? "text-cyan-100" : "text-violet-100"}
                  compact
                />
              </div>
              <div className="flex flex-wrap items-center gap-1.5">
                <StatusIndicator status={status} compact />
                <p className="text-[11px] text-slate-400">{optimized ? "AI-balanced" : "Live"}</p>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusPanel({ status, message }) {
  const style = STATUS_STYLES[status];

  return (
    <div className={`rounded-3xl border p-3.5 ${style.panel}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] uppercase tracking-[0.22em] text-slate-300/70">Status</p>
          <h3 className="mt-1.5 text-base font-semibold text-white">Network Health</h3>
        </div>
        <StatusIndicator status={status} />
      </div>
      <p className="mt-2.5 text-sm leading-6 text-slate-200/90">{message}</p>
    </div>
  );
}

function SmartInsightPanel({ insight, status }) {
  const style = STATUS_STYLES[status];

  return (
    <div className={`rounded-3xl border p-3.5 ${style.panel}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-[11px] uppercase tracking-[0.22em] text-slate-300/70">Smart Insight</p>
          <h3 className="mt-1.5 text-base font-semibold text-white">{insight.headline}</h3>
        </div>
        <StatusIndicator status={status} />
      </div>

      <p className="mt-2.5 text-sm leading-6 text-slate-200/90">{insight.summary}</p>

      <div className="mt-3 grid gap-2">
        {insight.points.map((item) => (
          <div
            key={item}
            className="rounded-2xl border border-white/[0.08] bg-slate-950/25 px-3 py-2 text-sm leading-6 text-slate-300"
          >
            {item}
          </div>
        ))}
      </div>
    </div>
  );
}

function AlertBar({ alert, isLiveMode, isSyncing, onToggle, toggleLabel, streamLocked = false }) {
  const style = STATUS_STYLES[alert.level];
  const icon = alert.level === "Critical" ? "🚨" : alert.level === "Warning" ? "⚠️" : "🟢";
  const emphasisClass =
    alert.level === "Critical" ? "alert-critical" : alert.level === "Warning" ? "alert-warning" : "alert-normal";

  return (
    <div
      className={`rounded-3xl border px-4 py-3 ${style.panel} ${emphasisClass} ${
        alert.level === "Critical" ? "critical-pulse" : ""
      }`}
    >
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-base leading-none">{icon}</span>
            <p className="text-sm font-semibold text-white">{alert.title}</p>
          </div>
          <p className="mt-1 text-sm text-slate-200/90">{alert.message}</p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-sm text-slate-200">
            <span className="live-dot h-2.5 w-2.5 rounded-full bg-cyan-300" />
            LIVE
          </div>
          <StatusIndicator status={alert.level} compact />
          <button
            className={`rounded-full border px-3 py-1 text-sm transition disabled:cursor-not-allowed disabled:opacity-60 ${
              isLiveMode
                ? "border-cyan-400/20 bg-cyan-500/10 text-cyan-100"
                : "border-white/10 bg-white/[0.04] text-slate-300"
            }`}
            disabled={streamLocked}
            onClick={onToggle}
          >
            {toggleLabel ?? (isLiveMode ? (isSyncing ? "Syncing..." : "Pause stream") : "Resume stream")}
          </button>
        </div>
      </div>
    </div>
  );
}

function AllocationCard({ item }) {
  const isReroute = item.from_channel !== item.to_channel;
  const fromLabel = String(item.from_channel ?? "").replace(/_/g, " ");
  const toLabel = String(item.to_channel ?? "").replace(/_/g, " ");

  return (
    <div className={`reroute-card rounded-2xl border border-white/[0.08] bg-white/[0.03] p-3 ${isReroute ? "reroute-active" : ""}`}>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-white">{item.user}</p>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-[0.12em]">
              <span className={`rounded-full border px-2 py-1 ${isReroute ? "route-source border-rose-400/20 bg-rose-500/10 text-rose-100" : "border-white/10 bg-white/[0.04] text-slate-200"}`}>
                {fromLabel}
              </span>
              <span className="reroute-arrow text-slate-400">→</span>
              <span className={`rounded-full border px-2 py-1 ${isReroute ? "route-target border-emerald-400/20 bg-emerald-500/10 text-emerald-100" : "border-white/10 bg-white/[0.04] text-slate-200"}`}>
                {toLabel}
              </span>
            </div>
          </div>
          <div className="rounded-full border border-white/[0.08] bg-slate-950/30 px-3 py-1 text-[11px] uppercase tracking-[0.12em] text-slate-300">
            {item.priority}
          </div>
        </div>

        <div className="grid gap-2 sm:grid-cols-2">
          <StatTile label="Confidence" value={`${formatNumber(item.confidence)}%`} accent="text-cyan-100" />
          <StatTile label="Gain" value={`${formatNumber(item.gain)} pts`} accent="text-pink-100" />
        </div>
      </div>
    </div>
  );
}

function PresetButton({ preset, onSelect }) {
  return (
    <button
      className="rounded-2xl border border-white/10 bg-white/[0.04] px-3.5 py-2.5 text-left transition hover:border-cyan-300/30 hover:bg-cyan-500/10 md:min-w-[132px]"
      onClick={() => onSelect(preset)}
    >
      <div className="text-sm font-medium text-slate-100">{preset.label}</div>
      <div className="mt-1 text-[10px] uppercase tracking-[0.16em] text-slate-500">{preset.hint}</div>
    </button>
  );
}

function ModeToggleButton({ mode, active, onClick }) {
  return (
    <button
      className={`rounded-full border px-3.5 py-2 text-sm font-medium transition ${
        active
          ? "border-cyan-400/24 bg-cyan-500/12 text-cyan-100"
          : "border-white/10 bg-white/[0.03] text-slate-300 hover:border-cyan-300/20 hover:bg-cyan-500/8"
      }`}
      onClick={onClick}
    >
      {mode.label}
    </button>
  );
}

function DeviceCard({ device, highlighted = false }) {
  const detailTiles = [
    { label: "Interference", value: `${formatNumber(device.computed_interference)}%`, accent: "text-white" },
    { label: "Latency", value: `${formatNumber(device.latency_ms)} ms`, accent: "text-cyan-100" },
    device.throughput_mbps == null
      ? null
      : { label: "Throughput", value: `${formatNumber(device.throughput_mbps)} Mbps`, accent: "text-violet-100" },
    { label: "Jitter", value: `${formatNumber(device.jitter_ms)} ms`, accent: "text-pink-100" },
  ].filter(Boolean);
  const noteBits = [
    `score ${formatNumber(device.performance_score)}%`,
    `jitter ${formatNumber(device.jitter_ms)} ms`,
  ];

  return (
    <div className={`glass-panel rounded-3xl p-3.5 sm:p-4 ${highlighted ? "channel-glow ring-1 ring-cyan-300/30" : ""}`}>
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-start justify-between gap-2.5">
          <div className="min-w-0">
            <p className="text-[9px] uppercase tracking-[0.14em] text-slate-500">Live Device</p>
            <h3 className="mt-1 text-[1.25rem] font-semibold leading-[1.05] text-white [overflow-wrap:anywhere] sm:text-[1.55rem]">
              {device.device_id}
            </h3>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {highlighted ? (
              <div className="rounded-full border border-cyan-400/20 bg-cyan-500/10 px-2.5 py-1 text-[10px] leading-tight text-cyan-100">
                anchor
              </div>
            ) : null}
            <div className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] leading-tight text-slate-300">
              {device.sample_count} samples
            </div>
          </div>
        </div>

        <div className="grid gap-2.5 lg:grid-cols-[92px_minmax(0,1fr)] lg:items-center">
          <DialGauge label="Load" value={device.computed_load} color="#63f3ff" compact />
          <div className="grid gap-1.5">
            <div className="grid gap-2 sm:grid-cols-2">
              {detailTiles.map((item) => (
                <StatTile key={item.label} label={item.label} value={item.value} accent={item.accent} compact />
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <StatusIndicator status={device.status} compact />
              <div className="rounded-full border border-white/10 bg-white/[0.04] px-2.5 py-1 text-[10px] text-slate-300">
                spread {formatNumber(device.latency_spread_ms)} ms
              </div>
              <p className="text-[11px] text-slate-400">{noteBits.join(" · ")}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ErrorCard({ error }) {
  return (
    <div className="rounded-2xl border border-rose-400/20 bg-rose-500/10 p-3 text-sm text-rose-100">{error}</div>
  );
}

function ChartToggle({ label, active, onClick }) {
  return (
    <button
      className={`rounded-full border px-3 py-1.5 text-xs font-medium transition ${
        active
          ? "border-cyan-400/24 bg-cyan-500/12 text-cyan-100"
          : "border-white/10 bg-white/[0.03] text-slate-300 hover:border-cyan-300/20 hover:bg-cyan-500/8"
      }`}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

function EmptyDecisionState() {
  return (
    <div className="mt-6 rounded-3xl border border-dashed border-white/10 bg-white/[0.02] p-8 text-center text-slate-400">
      Run AI Allocation to populate the optimization output.
    </div>
  );
}

function ChartPanel({ title, subtitle, labels, datasets, yTitle, compact = false, maxY = 100 }) {
  const canvasRef = useRef(null);
  const chartRef = useRef(null);

  useEffect(() => {
    if (!canvasRef.current || !labels.length || !datasets.length) {
      return undefined;
    }

    chartRef.current?.destroy();
    chartRef.current = new Chart(canvasRef.current, {
      type: "line",
      data: { labels, datasets },
      options: {
        maintainAspectRatio: false,
        interaction: { intersect: false, mode: "index" },
        animation: { duration: 850, easing: "easeOutQuart" },
        plugins: {
          legend: {
            labels: {
              color: "#cbd5e1",
              usePointStyle: true,
              padding: 18,
            },
          },
          tooltip: {
            backgroundColor: "rgba(9, 12, 26, 0.92)",
            borderColor: "rgba(255,255,255,0.08)",
            borderWidth: 1,
            titleColor: "#fff",
            bodyColor: "#dbeafe",
          },
        },
        scales: {
          x: {
            grid: { color: "rgba(148, 163, 184, 0.08)" },
            ticks: { color: "#94a3b8" },
          },
          y: {
            beginAtZero: true,
            max: maxY,
            grid: { color: "rgba(148, 163, 184, 0.08)" },
            ticks: { color: "#94a3b8" },
            title: { display: true, text: yTitle, color: "#94a3b8" },
          },
        },
      },
    });

    return () => chartRef.current?.destroy();
  }, [datasets, labels, maxY, yTitle]);

  return (
    <div className={compact ? "" : "glass-panel rounded-[28px] p-4 sm:p-5"}>
      {!compact ? (
        <div className="mb-4">
          <p className="text-[11px] uppercase tracking-[0.22em] text-cyan-300/70">{title}</p>
          <p className="mt-2 text-sm leading-6 text-slate-400">{subtitle}</p>
        </div>
      ) : null}
      <div className={`relative ${compact ? "h-40 sm:h-44" : "h-44 sm:h-48"}`}>
        {labels.length && datasets.length ? (
          <canvas ref={canvasRef} />
        ) : (
          <div className="flex h-full items-center justify-center rounded-2xl border border-dashed border-white/[0.08] bg-white/[0.02] text-sm text-slate-500">
            Waiting for live chart data
          </div>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const [appMode, setAppMode] = useState("simulation");
  const [controls, setControls] = useState(DEFAULT_CONTROLS);
  const [activeControls, setActiveControls] = useState(DEFAULT_CONTROLS);
  const [simulation, setSimulation] = useState(null);
  const [decision, setDecision] = useState(null);
  const [streamEvent, setStreamEvent] = useState(null);
  const [loadingSimulation, setLoadingSimulation] = useState(true);
  const [loadingDecision, setLoadingDecision] = useState(true);
  const [isLiveMode, setIsLiveMode] = useState(true);
  const [isLiveSyncing, setIsLiveSyncing] = useState(false);
  const [interferenceDelta, setInterferenceDelta] = useState(0);
  const [activeChart, setActiveChart] = useState("usage");
  const [activeMiddlePanel, setActiveMiddlePanel] = useState("spectrum");
  const [error, setError] = useState("");
  const [networkFrame, setNetworkFrame] = useState(null);
  const [networkLoading, setNetworkLoading] = useState(true);
  const [networkError, setNetworkError] = useState("");
  const liveSeedRef = useRef(makeSeed());
  const previousInterferenceRef = useRef(null);
  const socketRef = useRef(null);
  const [streamVersion, setStreamVersion] = useState(0);
  const networkSocketRef = useRef(null);
  const [networkStreamVersion, setNetworkStreamVersion] = useState(0);

  const applyFrame = (frame) => {
    if (!frame?.simulation || !frame?.decision) {
      return;
    }

    const nextInterference = frame.simulation.metrics.interference;
    setInterferenceDelta(
      previousInterferenceRef.current == null ? 0 : Number((nextInterference - previousInterferenceRef.current).toFixed(1)),
    );
    previousInterferenceRef.current = nextInterference;
    setSimulation(frame.simulation);
    setDecision(frame.decision);
    setStreamEvent(frame.event ?? null);
    setLoadingSimulation(false);
    setLoadingDecision(false);
    setIsLiveSyncing(false);
    setError("");
  };

  const applyNetworkFrame = (frame) => {
    if (!frame?.metrics || !Array.isArray(frame?.devices)) {
      return;
    }

    setNetworkFrame(frame);
    setNetworkLoading(false);
    setNetworkError("");
  };

  useEffect(() => {
    setActiveMiddlePanel(appMode === "simulation" ? "spectrum" : "devices");
    setActiveChart(appMode === "simulation" ? "usage" : "latency");
  }, [appMode]);

  const fetchSnapshot = async (nextControls = activeControls, mode = "scenario", reset = false) => {
    if (reset) {
      liveSeedRef.current = makeSeed();
      previousInterferenceRef.current = null;
      setInterferenceDelta(0);
    }

    if (mode === "scenario") {
      setLoadingSimulation(true);
      setLoadingDecision(true);
    }
    if (mode === "decision") {
      setLoadingDecision(true);
    }
    setIsLiveSyncing(mode === "silent");
    setError("");

    try {
      const artificialDelay = mode === "decision" ? 650 : mode === "scenario" ? 260 : 0;
      const [data] = await Promise.all([
        postJSON("/api/live", toRequestPayload(nextControls, liveSeedRef.current, 0)),
        artificialDelay ? sleep(artificialDelay) : Promise.resolve(),
      ]);
      applyFrame(data);
    } catch (err) {
      setLoadingSimulation(false);
      setLoadingDecision(false);
      setIsLiveSyncing(false);
      setError(err.message || "Unable to refresh live spectrum data");
    }
  };

  const sendSocketMessage = (message) => {
    const socket = socketRef.current;
    if (socket && socket.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify(message));
      return true;
    }
    return false;
  };

  const runSimulation = async (nextControls = controls) => {
    setActiveMiddlePanel("spectrum");
    setActiveControls(nextControls);
    liveSeedRef.current = makeSeed();
    previousInterferenceRef.current = null;
    setInterferenceDelta(0);
    setLoadingSimulation(true);
    setLoadingDecision(true);
    setError("");

    if (isLiveMode) {
      setStreamVersion((current) => current + 1);
      return;
    }

    await fetchSnapshot(nextControls, "scenario", false);
  };

  const runOptimization = async () => {
    setActiveMiddlePanel("decision");
    setLoadingDecision(true);
    setIsLiveSyncing(true);

    if (isLiveMode) {
      if (!sendSocketMessage({ type: "snapshot" })) {
        setStreamVersion((current) => current + 1);
      }
      return;
    }

    await fetchSnapshot(activeControls, "decision", false);
  };

  useEffect(() => {
    if (appMode !== "simulation") {
      socketRef.current?.close();
      socketRef.current = null;
      setIsLiveSyncing(false);
      return undefined;
    }

    if (!isLiveMode) {
      socketRef.current?.close();
      socketRef.current = null;
      setIsLiveSyncing(false);
      if (!simulation) {
        void fetchSnapshot(activeControls, "scenario", true);
      }
      return undefined;
    }

    const socket = new WebSocket(buildSocketUrl());
    socketRef.current = socket;
    let cancelled = false;

    setIsLiveSyncing(true);

    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          type: "config",
          reset: true,
          payload: toRequestPayload(activeControls, liveSeedRef.current, 0),
        }),
      );
    };

    socket.onmessage = (event) => {
      if (cancelled) {
        return;
      }

      try {
        const frame = JSON.parse(event.data);
        applyFrame(frame);
      } catch {
        setLoadingSimulation(false);
        setLoadingDecision(false);
        setIsLiveSyncing(false);
        setError("Live stream payload could not be parsed");
      }
    };

    socket.onerror = () => {
      if (!cancelled) {
        setError("Live stream interrupted. Reconnecting...");
      }
    };

    socket.onclose = () => {
      if (socketRef.current === socket) {
        socketRef.current = null;
      }

      if (!cancelled) {
        setIsLiveSyncing(false);
        setError("Live stream disconnected. Reconnecting...");
        window.setTimeout(() => {
          setStreamVersion((current) => current + 1);
        }, 1200);
      }
    };

    return () => {
      cancelled = true;
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
      socket.close();
    };
  }, [activeControls, appMode, isLiveMode, streamVersion]);

  useEffect(() => {
    if (appMode !== "live-network") {
      networkSocketRef.current?.close();
      networkSocketRef.current = null;
      return undefined;
    }

    const socket = new WebSocket(buildSocketUrl("/ws/network"));
    networkSocketRef.current = socket;
    let cancelled = false;

    setNetworkLoading(true);
    setNetworkError("");

    socket.onmessage = (event) => {
      if (cancelled) {
        return;
      }

      try {
        const frame = JSON.parse(event.data);
        applyNetworkFrame(frame);
      } catch {
        setNetworkLoading(false);
        setNetworkError("Live network payload could not be parsed");
      }
    };

    socket.onerror = () => {
      if (!cancelled) {
        setNetworkError("Live network stream interrupted. Reconnecting...");
      }
    };

    socket.onclose = () => {
      if (networkSocketRef.current === socket) {
        networkSocketRef.current = null;
      }

      if (!cancelled) {
        setNetworkError("Live network stream disconnected. Reconnecting...");
        window.setTimeout(() => {
          setNetworkStreamVersion((current) => current + 1);
        }, 1200);
      }
    };

    return () => {
      cancelled = true;
      if (networkSocketRef.current === socket) {
        networkSocketRef.current = null;
      }
      socket.close();
    };
  }, [appMode, networkStreamVersion]);

  const usageChart = useMemo(() => {
    if (!simulation) {
      return { labels: [], datasets: [] };
    }

    return {
      labels: simulation.timeseries.map((point) => point.tick),
      datasets: simulation.channels.map((channel, index) => ({
        label: channel.id,
        data: simulation.timeseries.map((point) => point.channels[channel.id]),
        borderColor: CHANNEL_COLORS[index % CHANNEL_COLORS.length],
        backgroundColor: `${CHANNEL_COLORS[index % CHANNEL_COLORS.length]}20`,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.35,
      })),
    };
  }, [simulation]);

  const interferenceChart = useMemo(() => {
    if (!simulation) {
      return { labels: [], datasets: [] };
    }

    const datasets = [
      {
        label: "Baseline interference",
        data: simulation.timeseries.map((point) => point.interference),
        borderColor: "#bc7cff",
        backgroundColor: "rgba(188, 124, 255, 0.14)",
        fill: true,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.35,
      },
    ];

    if (decision) {
      const reductionFactor = Math.max(0.48, 1 - decision.metrics.interference_reduction / 115);
      datasets.push({
        label: "AI-optimized projection",
        data: simulation.timeseries.map((point, index) => Number((point.interference * reductionFactor + Math.sin(index / 3) * 1.4).toFixed(1))),
        borderColor: "#63f3ff",
        backgroundColor: "rgba(99, 243, 255, 0.08)",
        fill: true,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.35,
      });
    }

    return {
      labels: simulation.timeseries.map((point) => point.tick),
      datasets,
    };
  }, [decision, simulation]);

  const networkScoreChart = useMemo(() => {
    if (!simulation) {
      return { labels: [], datasets: [] };
    }

    const baselineScores = simulation.timeseries.map((point) =>
      Number(
        clampNumber(100 - point.interference * 0.55 - point.usage * 0.18 + simulation.metrics.fairness * 0.22, 18, 99).toFixed(1),
      ),
    );
    const datasets = [
      {
        label: "Network score",
        data: baselineScores,
        borderColor: "#f59e0b",
        backgroundColor: "rgba(245, 158, 11, 0.08)",
        fill: true,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.35,
      },
    ];

    if (decision) {
      datasets.push({
        label: "AI score projection",
        data: baselineScores.map((value, index) =>
          Number(clampNumber(value + decision.metrics.interference_reduction * 0.35 + Math.sin(index / 3.3) * 1.2, 20, 99).toFixed(1)),
        ),
        borderColor: "#22d3ee",
        backgroundColor: "rgba(34, 211, 238, 0.06)",
        fill: true,
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.35,
      });
    }

    return {
      labels: simulation.timeseries.map((point) => point.tick),
      datasets,
    };
  }, [decision, simulation]);

  const pressureChart = useMemo(() => {
    if (!simulation) {
      return { labels: [], datasets: [] };
    }

    const labels = simulation.timeseries.map((point) => point.tick);
    const baselineNoise = simulation.timeseries.map((point, index) =>
      Number(
        clampNumber(
          simulation.config.noise_level * 0.62 + point.interference * 0.22 + Math.cos(index / 3.5) * 3.4,
          0,
          100,
        ).toFixed(1),
      ),
    );
    const datasets = [
      {
        label: "Load",
        data: simulation.timeseries.map((point) => point.usage),
        borderColor: "#63f3ff",
        backgroundColor: "rgba(99, 243, 255, 0.08)",
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.35,
      },
      {
        label: "Interference",
        data: simulation.timeseries.map((point) => point.interference),
        borderColor: "#bc7cff",
        backgroundColor: "rgba(188, 124, 255, 0.08)",
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.35,
      },
      {
        label: "Noise",
        data: baselineNoise,
        borderColor: "#f59e0b",
        backgroundColor: "rgba(245, 158, 11, 0.08)",
        pointRadius: 0,
        borderWidth: 2,
        tension: 0.35,
      },
    ];

    if (decision) {
      const reductionFactor = Math.max(0.5, 1 - decision.metrics.interference_reduction / 120);
      datasets.push(
        {
          label: "AI load projection",
          data: simulation.timeseries.map((point) =>
            Number(clampNumber(point.usage * (0.92 - decision.metrics.interference_reduction / 320), 5, 98).toFixed(1)),
          ),
          borderColor: "#22d3ee",
          backgroundColor: "rgba(34, 211, 238, 0.04)",
          pointRadius: 0,
          borderWidth: 2,
          borderDash: [6, 5],
          tension: 0.35,
        },
        {
          label: "AI interference projection",
          data: simulation.timeseries.map((point, index) =>
            Number(clampNumber(point.interference * reductionFactor + Math.sin(index / 3) * 1.2, 3, 96).toFixed(1)),
          ),
          borderColor: "#f472b6",
          backgroundColor: "rgba(244, 114, 182, 0.04)",
          pointRadius: 0,
          borderWidth: 2,
          borderDash: [6, 5],
          tension: 0.35,
        },
        {
          label: "AI noise projection",
          data: baselineNoise.map((value, index) =>
            Number(clampNumber(value * (0.9 - decision.metrics.interference_reduction / 360) + Math.cos(index / 4.2), 0, 100).toFixed(1)),
          ),
          borderColor: "#fb7185",
          backgroundColor: "rgba(251, 113, 133, 0.04)",
          pointRadius: 0,
          borderWidth: 2,
          borderDash: [6, 5],
          tension: 0.35,
        },
      );
    }

    return { labels, datasets };
  }, [decision, simulation]);

  const systemStatus = useMemo(() => getSystemStatus(simulation, decision), [decision, simulation]);

  const statusMessage = useMemo(() => {
    if (!simulation) {
      return "The simulator is ready to evaluate the current spectrum state.";
    }

    if (decision) {
      return streamEvent?.triggered_ai
        ? `Triggered reroute is holding projected interference near ${formatNumber(
            decision.metrics.optimized_interference,
          )}% while efficiency stays at ${formatNumber(decision.metrics.efficiency)}%.`
        : `AI is in watch mode, holding ${decision.agent.selected_channel} as the best reserve while projected interference stays near ${formatNumber(
            decision.metrics.optimized_interference,
          )}%.`;
    }

    return `Current baseline shows ${formatNumber(simulation.metrics.interference)}% interference with ${formatNumber(simulation.metrics.efficiency)}% efficiency before optimization.`;
  }, [decision, simulation, streamEvent]);

  const smartInsight = useMemo(() => {
    if (!simulation) {
      return {
        headline: "Simulator standing by",
        summary: "Run a scenario to generate congestion, throughput, and allocation signals.",
        points: ["Controls define users, noise, and bandwidth for the next run."],
      };
    }

    const busiestChannel = [...simulation.channels].sort((left, right) => right.interference - left.interference)[0];
    const cleanestChannel = [...simulation.channels].sort((left, right) => left.interference - right.interference)[0];

    if (decision) {
      return {
        headline: `${decision.agent.selected_channel} is the best immediate route`,
        summary: streamEvent?.triggered_ai
          ? `Threshold logic fired on ${streamEvent.channel}, so Spectrum Pilot is actively shifting pressure toward ${decision.agent.selected_channel} and keeping ${decision.agent.backup_channel} as fallback.`
          : `Spectrum Pilot is monitoring drift, keeping ${decision.agent.selected_channel} as the top route and ${decision.agent.backup_channel} ready if a spike lands.`,
        points: [
          `AI Decision Confidence: ${formatNumber(decision.agent.confidence)}%.`,
          `Reason: lowest interference ${decision.agent.reason_points[0]?.value}, highest headroom ${decision.agent.reason_points[1]?.value}, score gap ${decision.agent.reason_points[2]?.value}.`,
          `Action: ${decision.agent.action_text}`,
        ],
      };
    }

    return {
      headline: `${busiestChannel.id} is creating the most pressure`,
      summary: `${busiestChannel.id} currently carries the heaviest interference, while ${cleanestChannel.id} has the cleanest conditions and the best chance to absorb extra demand.`,
      points: [
        `${formatNumber(simulation.metrics.interference)}% baseline interference is limiting overall efficiency.`,
        `${formatNumber(simulation.metrics.fairness)}% fairness means channel load is not evenly distributed.`,
        `Running AI allocation should redirect demand toward lower-pressure channels.`,
      ],
    };
  }, [decision, simulation, streamEvent]);

  const alertInfo = useMemo(() => {
    if (streamEvent) {
      return {
        level: streamEvent.level,
        title: streamEvent.title,
        message: streamEvent.message,
      };
    }

    if (!simulation) {
      return {
        level: "Normal",
        title: "Spectrum monitor online",
        message: "Initializing live telemetry and AI scoring.",
      };
    }

    const busiestChannel = [...simulation.channels].sort((left, right) => right.interference - left.interference)[0];
    const deltaLabel = `${interferenceDelta >= 0 ? "+" : ""}${formatNumber(interferenceDelta)}%`;

    if (systemStatus === "Critical") {
      return {
        level: "Critical",
        title: `CRITICAL: ${busiestChannel.id} congestion detected`,
        message: decision
          ? `Interference spike ${deltaLabel} — rerouting devices toward ${decision.agent.selected_channel}.`
          : `Interference spike ${deltaLabel} — manual optimization recommended.`,
      };
    }

    if (systemStatus === "Warning") {
      return {
        level: "Warning",
        title: `WARNING: ${busiestChannel.id} load is climbing`,
        message: decision
          ? `AI is balancing demand and keeping ${decision.agent.backup_channel} ready as the fallback path.`
          : "Noise and congestion are rising across the current slice.",
      };
    }

    return {
      level: "Normal",
      title: "Normal: spectrum conditions are stable",
      message: decision
        ? `AI is holding the strongest route on ${decision.agent.selected_channel}.`
        : "Waiting for the next optimization cycle to rebalance channels.",
    };
  }, [decision, interferenceDelta, simulation, streamEvent, systemStatus]);

  const metrics = useMemo(() => {
    return [
      {
        label: "Performance",
        value: `${formatNumber(decision?.metrics.efficiency ?? simulation?.metrics.efficiency)}%`,
        helper: decision ? `Up from ${formatNumber(decision.metrics.baseline_efficiency)}% baseline` : "Baseline efficiency",
        accent: "violet",
      },
      {
        label: "Pressure",
        value: `${formatNumber(decision?.metrics.optimized_interference ?? simulation?.metrics.interference)}%`,
        helper: decision
          ? `${formatNumber(decision.metrics.interference_reduction)}% lower than baseline`
          : `${formatNumber(simulation?.metrics.interference)}% current interference`,
        accent: "cyan",
      },
      {
        label: "Capacity",
        value: `${formatNumber(decision?.metrics.throughput ?? simulation?.metrics.throughput)} Mbps`,
        helper: decision ? "AI-optimized throughput" : "Baseline throughput",
        accent: "pink",
      },
    ];
  }, [decision, simulation]);

  const chartPanels = useMemo(
    () => [
      {
        key: "usage",
        label: "Usage",
        title: "Channel Usage Over Time",
        subtitle: "Real-time load movement across active channels.",
        labels: usageChart.labels,
        datasets: usageChart.datasets,
        yTitle: "Usage (%)",
      },
      {
        key: "interference",
        label: "Interference",
        title: "Interference vs Time",
        subtitle: "Baseline interference compared with AI-optimized projection.",
        labels: interferenceChart.labels,
        datasets: interferenceChart.datasets,
        yTitle: "Interference (%)",
      },
      {
        key: "score",
        label: "Network Score",
        title: "Total Network Score",
        subtitle: "Overall spectrum health improving over each live update.",
        labels: networkScoreChart.labels,
        datasets: networkScoreChart.datasets,
        yTitle: "Score",
      },
      {
        key: "pressure",
        label: "Pressure",
        title: "Load, Interference, and Noise",
        subtitle: "Baseline pressure compared with AI-optimized projections.",
        labels: pressureChart.labels,
        datasets: pressureChart.datasets,
        yTitle: "Pressure (%)",
      },
    ],
    [interferenceChart, networkScoreChart, pressureChart, usageChart],
  );

  const activeChartPanel = chartPanels.find((panel) => panel.key === activeChart) ?? chartPanels[0];
  const middlePanels = [
    { key: "spectrum", label: "Observed" },
    { key: "decision", label: "AI Decisions" },
  ];

  const spectrumPanelMeta = simulation ? (
    <div className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-300">
      {simulation.config.channel_count} channels active
    </div>
  ) : null;

  const decisionPanelMeta = decision ? (
    <div className="rounded-full border border-cyan-400/20 bg-cyan-500/10 px-3 py-1 text-xs text-cyan-100">
      {`${decision.decision_latency_ms} ms`}
    </div>
  ) : null;
  const activeMiddleMeta = activeMiddlePanel === "spectrum" ? spectrumPanelMeta : decisionPanelMeta;

  const spectrumPanelBody = (
    <div className="grid gap-3 lg:grid-cols-2">
      {simulation?.channels?.map((channel, index) => (
        <ChannelCard
          key={channel.id}
          channel={channel}
          index={index}
          optimized={false}
          highlighted={decision?.agent.selected_channel === channel.id}
        />
      ))}
    </div>
  );

  const decisionPanelBody = loadingDecision ? (
    <div className="space-y-3">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="shimmer rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4">
          <div className="h-4 w-24 rounded-full bg-white/10" />
          <div className="mt-3 h-3 w-full rounded-full bg-white/5" />
        </div>
      ))}
    </div>
  ) : decision ? (
    <>
      <div className="rounded-3xl border border-cyan-400/14 bg-cyan-500/[0.06] p-4 text-sm leading-7 text-slate-300">
        {decision.summary}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <StatTile
          label="Baseline Efficiency"
          value={`${formatNumber(decision.metrics.baseline_efficiency)}%`}
          accent="text-violet-100"
        />
        <StatTile
          label="Projected Interference"
          value={`${formatNumber(decision.metrics.optimized_interference)}%`}
          accent="text-pink-100"
        />
        <StatTile
          label="Throughput Lift"
          value={`${formatNumber((decision.metrics.throughput ?? 0) - (simulation?.metrics.throughput ?? 0))} Mbps`}
          accent="text-cyan-100"
        />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {decision.allocations.slice(0, 4).map((item) => (
          <AllocationCard key={item.user} item={item} />
        ))}
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {decision.optimized_channels.map((channel, index) => (
          <ChannelCard
            key={channel.id}
            channel={channel}
            index={index}
            optimized
            highlighted={decision.agent.selected_channel === channel.id}
          />
        ))}
      </div>
    </>
  ) : (
    <EmptyDecisionState />
  );

  const liveNetworkStatus = useMemo(() => getLiveNetworkStatus(networkFrame), [networkFrame]);
  const liveNetworkAlert = useMemo(
    () =>
      networkFrame?.event || {
        level: "Normal",
        title: "Waiting for live devices",
        message: "No real telemetry has been received yet.",
      },
    [networkFrame],
  );
  const liveNetworkMetrics = useMemo(
    () => {
      const baselinePoint = networkFrame?.timeseries?.[0];
      const baselineScore = baselinePoint?.score ?? networkFrame?.metrics.score ?? 0;
      const baselineInterference = baselinePoint?.interference ?? networkFrame?.metrics.interference ?? 0;
      const currentScore = networkFrame?.metrics.score ?? 0;
      const currentInterference = networkFrame?.metrics.interference ?? 0;
      const pressureDelta =
        baselineInterference > 0 ? ((baselineInterference - currentInterference) / baselineInterference) * 100 : 0;

      return [
        {
          label: "Performance",
          value: `${formatNumber(currentScore)}%`,
          helper:
            networkFrame?.timeseries?.length > 1
              ? `Up from ${formatNumber(baselineScore)}% baseline`
              : "Current live performance",
          accent: "violet",
        },
        {
          label: "Pressure",
          value: `${formatNumber(currentInterference)}%`,
          helper:
            networkFrame?.timeseries?.length > 1
              ? `${formatNumber(Math.abs(pressureDelta))}% ${pressureDelta >= 0 ? "lower" : "above"} baseline`
              : "Current live interference",
          accent: "cyan",
        },
        {
          label: "Capacity",
          value: `${formatNumber(networkFrame?.metrics.throughput_mbps)} Mbps`,
          helper: "Current live throughput",
          accent: "pink",
        },
      ];
    },
    [networkFrame],
  );
  const liveNetworkInsight = useMemo(() => {
    if (!networkFrame?.devices?.length) {
      return {
        headline: "Live network mode is ready",
        summary: "Live mode watches only real device telemetry, compares performance, and recommends actions without adding simulated values.",
        points: [
          "POST telemetry to /api/network/devices.",
          "Required fields: device_id, latency_ms, throughput_mbps, jitter_ms, and packet_loss.",
          "AI decisions update automatically from the incoming device stream.",
        ],
      };
    }

    const anchorDevice = [...networkFrame.devices].sort((left, right) => right.performance_score - left.performance_score)[0];
    const congestedDevice = [...networkFrame.devices].sort((left, right) => left.performance_score - right.performance_score)[0];

    return {
      headline: `${anchorDevice.device_id} is the strongest live anchor`,
      summary:
        networkFrame.agent.status === "active"
          ? `${congestedDevice.device_id} is the weakest performer right now, so the live decision engine is recommending action around ${anchorDevice.device_id}.`
          : `${anchorDevice.device_id} currently has the cleanest observed profile while ${congestedDevice.device_id} is the device to keep watching.`,
      points: [
        `Observe: ${formatNumber(anchorDevice.latency_ms)} ms latency on ${anchorDevice.device_id}, ${formatNumber(congestedDevice.latency_ms)} ms on ${congestedDevice.device_id}.`,
        `Compare: ${formatNumber(anchorDevice.performance_score)}% vs ${formatNumber(congestedDevice.performance_score)}%.`,
        `Recommend: ${networkFrame.agent.action_text}`,
      ],
    };
  }, [networkFrame]);
  const networkChartPanels = useMemo(() => {
    if (!networkFrame) {
      return [];
    }

    return [
      {
        key: "usage",
        label: "Usage",
        title: "Live Load Over Time",
        subtitle: "Computed load moving across real device updates.",
        labels: networkFrame.timeseries.map((point) => point.tick),
        datasets: [
          {
            label: "Load",
            data: networkFrame.timeseries.map((point) => point.load),
            borderColor: "#63f3ff",
            backgroundColor: "rgba(99, 243, 255, 0.12)",
            fill: true,
            pointRadius: 0,
            borderWidth: 2,
            tension: 0.35,
          },
        ],
        yTitle: "Load (%)",
        maxY: 100,
      },
      {
        key: "interference",
        label: "Interference",
        title: "Interference vs Time",
        subtitle: "Observed interference across the live network stream.",
        labels: networkFrame.timeseries.map((point) => point.tick),
        datasets: [
          {
            label: "Interference",
            data: networkFrame.timeseries.map((point) => point.interference),
            borderColor: "#f472b6",
            backgroundColor: "rgba(244, 114, 182, 0.08)",
            pointRadius: 0,
            borderWidth: 2,
            tension: 0.35,
          },
        ],
        yTitle: "Interference (%)",
        maxY: 100,
      },
      {
        key: "score",
        label: "Network Score",
        title: "Live Network Score",
        subtitle: "Overall health computed from real device latency, jitter, loss, and throughput.",
        labels: networkFrame.timeseries.map((point) => point.tick),
        datasets: [
          {
            label: "Network score",
            data: networkFrame.timeseries.map((point) => point.score),
            borderColor: "#22d3ee",
            backgroundColor: "rgba(34, 211, 238, 0.08)",
            fill: true,
            pointRadius: 0,
            borderWidth: 2,
            tension: 0.35,
          },
        ],
        yTitle: "Score",
        maxY: 100,
      },
      {
        key: "pressure",
        label: "Pressure",
        title: "Load, Interference, and Noise",
        subtitle: "Real telemetry pressure signals across the live network stream.",
        labels: networkFrame.timeseries.map((point) => point.tick),
        datasets: [
          {
            label: "Load",
            data: networkFrame.timeseries.map((point) => point.load),
            borderColor: "#63f3ff",
            backgroundColor: "rgba(99, 243, 255, 0.08)",
            pointRadius: 0,
            borderWidth: 2,
            tension: 0.35,
          },
          {
            label: "Interference",
            data: networkFrame.timeseries.map((point) => point.interference),
            borderColor: "#bc7cff",
            backgroundColor: "rgba(188, 124, 255, 0.08)",
            pointRadius: 0,
            borderWidth: 2,
            tension: 0.35,
          },
          {
            label: "Noise",
            data: networkFrame.timeseries.map((point) => point.noise),
            borderColor: "#f59e0b",
            backgroundColor: "rgba(245, 158, 11, 0.08)",
            pointRadius: 0,
            borderWidth: 2,
            tension: 0.35,
          },
        ],
        yTitle: "Pressure (%)",
        maxY: 100,
      },
    ];
  }, [networkFrame]);
  const networkMiddlePanels = [
    { key: "devices", label: "Observed" },
    { key: "decision", label: "AI Decisions" },
  ];
  const networkDevicesMeta = networkFrame ? (
    <div className="rounded-full border border-white/10 bg-white/[0.04] px-3 py-1 text-xs text-slate-300">
      {networkFrame.metrics.device_count} devices connected
    </div>
  ) : null;
  const networkDecisionMeta = networkFrame ? (
    <div className="rounded-full border border-cyan-400/20 bg-cyan-500/10 px-3 py-1 text-xs text-cyan-100">
      real telemetry only
    </div>
  ) : null;
  const networkDevicesBody = networkLoading ? (
    <div className="space-y-3">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="shimmer rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4">
          <div className="h-4 w-24 rounded-full bg-white/10" />
          <div className="mt-3 h-3 w-full rounded-full bg-white/5" />
        </div>
      ))}
    </div>
  ) : networkFrame?.devices?.length ? (
    <div className="grid gap-3 xl:grid-cols-2">
      {networkFrame.devices.map((device) => (
        <DeviceCard
          key={device.device_id}
          device={device}
          highlighted={networkFrame.agent.anchor_device === device.device_id}
        />
      ))}
    </div>
  ) : (
    <div className="rounded-3xl border border-dashed border-white/10 bg-white/[0.02] p-8 text-center text-slate-400">
      No live devices are connected yet. Devices can start publishing telemetry to `/api/network/devices`.
    </div>
  );
  const networkDecisionBody = networkLoading ? (
    <div className="space-y-3">
      {Array.from({ length: 4 }).map((_, index) => (
        <div key={index} className="shimmer rounded-2xl border border-white/[0.08] bg-white/[0.03] p-4">
          <div className="h-4 w-24 rounded-full bg-white/10" />
          <div className="mt-3 h-3 w-full rounded-full bg-white/5" />
        </div>
      ))}
    </div>
  ) : networkFrame ? (
    <>
      <div className="rounded-3xl border border-cyan-400/14 bg-cyan-500/[0.06] p-4 text-sm leading-7 text-slate-300">
        {networkFrame.summary}
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-3">
        <StatTile
          label="Anchor score"
          value={`${formatNumber(
            networkFrame.devices.find((device) => device.device_id === networkFrame.agent.anchor_device)?.performance_score ??
              0,
          )}%`}
          accent="text-violet-100"
        />
        <StatTile
          label="Observed interference"
          value={`${formatNumber(networkFrame.metrics.interference)}%`}
          accent="text-pink-100"
        />
        <StatTile
          label="Decision confidence"
          value={`${formatNumber(networkFrame.agent.confidence)}%`}
          accent="text-cyan-100"
        />
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {networkFrame.agent.allocations.map((item) => (
          <AllocationCard key={`${item.user}-${item.to_channel}`} item={item} />
        ))}
      </div>

      <div className="mt-4 grid gap-3 lg:grid-cols-2">
        {networkFrame.devices.map((device) => (
          <DeviceCard
            key={`decision-${device.device_id}`}
            device={device}
            highlighted={networkFrame.agent.anchor_device === device.device_id}
          />
        ))}
      </div>
    </>
  ) : (
    <EmptyDecisionState />
  );
  const currentChartPanels = appMode === "simulation" ? chartPanels : networkChartPanels;
  const currentChartPanel = currentChartPanels.find((panel) => panel.key === activeChart) ?? currentChartPanels[0];
  const currentMiddlePanels = appMode === "simulation" ? middlePanels : networkMiddlePanels;
  const currentMiddleMeta =
    appMode === "simulation"
      ? activeMiddleMeta
      : activeMiddlePanel === "devices"
        ? networkDevicesMeta
        : networkDecisionMeta;
  const currentMiddleBody =
    appMode === "simulation"
      ? activeMiddlePanel === "spectrum"
        ? spectrumPanelBody
        : decisionPanelBody
      : activeMiddlePanel === "devices"
        ? networkDevicesBody
        : networkDecisionBody;
  const currentMetrics = appMode === "simulation" ? metrics : liveNetworkMetrics;
  const currentAlert = appMode === "simulation" ? alertInfo : liveNetworkAlert;
  const currentError = appMode === "simulation" ? error : networkError;
  const currentStatus = appMode === "simulation" ? systemStatus : liveNetworkStatus;
  const currentStatusMessage =
    appMode === "simulation"
      ? statusMessage
      : networkFrame
        ? networkFrame.agent.status === "active"
          ? `Triggered reroute is holding projected interference near ${formatNumber(
              networkFrame.metrics.interference,
            )}% while performance stays at ${formatNumber(networkFrame.metrics.score)}%.`
          : `Live routing is holding ${networkFrame.agent.anchor_device} while interference stays near ${formatNumber(
              networkFrame.metrics.interference,
            )}% and performance stays at ${formatNumber(networkFrame.metrics.score)}%.`
        : "Live network mode is standing by for real device telemetry.";
  const currentInsight = appMode === "simulation" ? smartInsight : liveNetworkInsight;
  const currentMiddleSummary = useMemo(() => {
    if (appMode === "simulation") {
      const hottestChannel = simulation?.channels?.length
        ? [...simulation.channels].sort((left, right) => right.interference - left.interference)[0]
        : null;
      const comparisonChannel =
        decision?.agent.backup_channel ||
        (simulation?.channels?.length
          ? [...simulation.channels].sort((left, right) => left.interference - right.interference)[0]?.id
          : null);
      return [
        {
          label: "Observe",
          value: hottestChannel ? hottestChannel.id : "Waiting",
          accent: "text-cyan-100",
        },
        {
          label: "Compare",
          value: comparisonChannel ?? "Baseline only",
          accent: "text-violet-100",
        },
        {
          label: "Action",
          value: decision ? (decision.agent.action.mode === "reroute" ? "Reroute" : "Hold") : "Run AI",
          accent: decision?.agent.action.mode === "reroute" ? "text-pink-100" : "text-emerald-100",
        },
      ];
    }

    const leadDevice = networkFrame?.devices?.[0];
    const comparisonDevice = networkFrame?.devices?.[1] ?? leadDevice;
    return [
      {
        label: "Observe",
        value: leadDevice?.device_id ?? "Waiting",
        accent: "text-cyan-100",
      },
      {
        label: "Compare",
        value: comparisonDevice?.device_id ?? "No devices",
        accent: "text-violet-100",
      },
      {
        label: "Action",
        value: networkFrame?.agent?.status === "active" ? "Reroute" : "Hold",
        accent: networkFrame?.agent?.status === "active" ? "text-pink-100" : "text-emerald-100",
      },
    ];
  }, [appMode, decision, networkFrame, simulation]);

  return (
    <div className="relative min-h-screen overflow-hidden bg-ink font-body text-slate-100 antialiased">
      <div className="pulse-orb pointer-events-none absolute left-[10%] top-20 h-64 w-64 rounded-full bg-violet-500/12 blur-3xl" />
      <div className="pulse-orb pointer-events-none absolute bottom-16 right-[8%] h-72 w-72 rounded-full bg-cyan-400/10 blur-3xl" />

      <main className="mx-auto flex min-h-screen max-w-7xl items-center justify-center px-4 py-5 sm:px-6 lg:px-8">
        <section className="card-shell fade-in w-full rounded-[34px] border border-white/[0.08] bg-slate-950/75 p-5 sm:p-6 lg:p-7">
          <div className="grid gap-3 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-start">
            <div className="min-w-0 pr-0 lg:pr-4">
              <h1 className="max-w-[12ch] font-display text-[2.3rem] font-semibold leading-[0.92] tracking-tight text-white sm:text-[2.8rem] xl:max-w-none xl:text-[4.3rem]">
                AI-Driven Spectrum Sharing
              </h1>
            </div>

            <div className="grid gap-2 xl:justify-self-end">
              <div className="flex flex-wrap gap-2 xl:justify-end">
                {APP_MODES.map((mode) => (
                  <ModeToggleButton
                    key={mode.key}
                    mode={mode}
                    active={mode.key === appMode}
                    onClick={() => setAppMode(mode.key)}
                  />
                ))}
              </div>

              {appMode === "simulation" ? (
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-3 xl:justify-self-end">
                  {PRESETS.map((preset) => (
                    <PresetButton
                      key={preset.label}
                      preset={preset}
                      onSelect={(nextPreset) => {
                        setControls(nextPreset);
                        runSimulation(nextPreset);
                      }}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          </div>

          <div className="mt-4">
            <AlertBar
              alert={currentAlert}
              isLiveMode={appMode === "simulation" ? isLiveMode : true}
              isSyncing={appMode === "simulation" ? isLiveSyncing : networkLoading}
              onToggle={() => {
                if (appMode === "simulation") {
                  setIsLiveMode((current) => !current);
                }
              }}
              toggleLabel={appMode === "simulation" ? undefined : "Live only"}
              streamLocked={appMode === "live-network"}
            />
          </div>

          <div className="mt-4 grid items-start gap-4 xl:grid-cols-[minmax(296px,322px)_minmax(0,1fr)]">
            <div className="glass-panel self-start rounded-[30px] p-4">
              {appMode === "simulation" ? (
                <>
                  <PanelHeader
                    eyebrow="Controls"
                    title="Scenario Builder"
                    meta={
                      <button
                        className="rounded-full border border-cyan-400/20 bg-cyan-500/10 px-3.5 py-2 text-sm font-medium text-cyan-100 transition hover:bg-cyan-500/20"
                        onClick={() => runSimulation()}
                      >
                        {loadingSimulation ? "Refreshing..." : "Run Scenario"}
                      </button>
                    }
                  />

                  <div className="mt-4 space-y-3">
                    <Slider
                      label="Number of users"
                      value={controls.users}
                      min={2}
                      max={24}
                      step={1}
                      unit=""
                      onChange={(value) => setControls((current) => ({ ...current, users: value }))}
                    />
                    <Slider
                      label="Noise level"
                      value={controls.noiseLevel}
                      min={0}
                      max={100}
                      step={1}
                      unit="%"
                      onChange={(value) => setControls((current) => ({ ...current, noiseLevel: value }))}
                    />
                    <Slider
                      label="Bandwidth"
                      value={controls.bandwidth}
                      min={10}
                      max={100}
                      step={5}
                      unit=" MHz"
                      onChange={(value) => setControls((current) => ({ ...current, bandwidth: value }))}
                    />
                  </div>

                  <div className="mt-4 grid gap-3 sm:grid-cols-3">
                    <StatTile label="Users" value={String(controls.users)} accent="text-cyan-100" compact />
                    <StatTile label="Noise" value={`${controls.noiseLevel}%`} accent="text-violet-100" compact />
                    <StatTile label="Bandwidth" value={`${controls.bandwidth} MHz`} accent="text-pink-100" compact />
                  </div>

                  <div className="mt-4">
                    <button
                      className="w-full rounded-2xl border border-violet-300/20 bg-violet-500/15 px-4 py-3 text-sm font-medium text-violet-100 transition hover:bg-violet-500/25 disabled:cursor-not-allowed disabled:opacity-60"
                      disabled={loadingSimulation || loadingDecision || !simulation}
                      onClick={runOptimization}
                    >
                      {loadingDecision ? "Optimizing..." : "Recompute AI"}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <PanelHeader
                    eyebrow="Controls"
                    title="Live Device Stream"
                    meta={
                      <div className="rounded-full border border-cyan-400/20 bg-cyan-500/10 px-3 py-1 text-xs text-cyan-100">
                        {networkFrame?.metrics.device_count ?? 0} connected
                      </div>
                    }
                  />

                  <div className="mt-4 grid gap-3 sm:grid-cols-3">
                    <StatTile label="Connected" value={String(networkFrame?.metrics.device_count ?? 0)} accent="text-cyan-100" />
                    <StatTile
                      label="Avg latency"
                      value={networkFrame ? `${formatNumber(networkFrame.metrics.avg_latency_ms)} ms` : "0.0 ms"}
                      accent="text-violet-100"
                    />
                    <StatTile
                      label="Throughput"
                      value={networkFrame ? `${formatNumber(networkFrame.metrics.throughput_mbps)} Mbps` : "0.0 Mbps"}
                      accent="text-pink-100"
                    />
                  </div>

                </>
              )}

              <div className="mt-3.5">
                <StatusPanel status={currentStatus} message={currentStatusMessage} />
              </div>

              <div className="mt-3.5">
                <SmartInsightPanel insight={currentInsight} status={currentStatus} />
              </div>

              {currentError ? (
                <div className="mt-3.5">
                  <ErrorCard error={currentError} />
                </div>
              ) : null}
            </div>

            <div className="grid self-start gap-4">
              <div className="grid gap-3 md:grid-cols-3">
                {currentMetrics.map((metric) => (
                  <MetricCard key={metric.label} {...metric} />
                ))}
              </div>

              <div className="glass-panel rounded-[30px] p-5">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="text-[11px] uppercase tracking-[0.22em] text-cyan-300/70">Live Panels</p>
                    <h2 className="mt-2 text-xl font-semibold text-white">
                      {appMode === "simulation"
                        ? activeMiddlePanel === "spectrum"
                          ? "Observed State"
                          : "AI Decisions"
                        : activeMiddlePanel === "devices"
                          ? "Observed State"
                          : "AI Decisions"}
                    </h2>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {currentMiddlePanels.map((panel) => (
                      <ChartToggle
                        key={panel.key}
                        label={panel.label}
                        active={panel.key === activeMiddlePanel}
                        onClick={() => setActiveMiddlePanel(panel.key)}
                      />
                    ))}
                  </div>
                </div>

                <div className="mt-4 grid gap-3 sm:grid-cols-3">
                  {currentMiddleSummary.map((item) => (
                    <StatTile key={item.label} label={item.label} value={item.value} accent={item.accent} />
                  ))}
                </div>

                <div className="mt-4">
                  <div>
                    {currentMiddleMeta ? <div className="mb-4 flex justify-end">{currentMiddleMeta}</div> : null}
                    {currentMiddleBody}
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-4 2xl:hidden">
            <div className="glass-panel rounded-[28px] p-4 sm:p-5">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div className="min-w-0">
                  <p className="text-[11px] uppercase tracking-[0.22em] text-cyan-300/70">Live Analytics</p>
                  <h2 className="mt-2 text-xl font-semibold text-white">{currentChartPanel?.title ?? "Analytics"}</h2>
                  <p className="mt-2 text-sm leading-6 text-slate-400">{currentChartPanel?.subtitle ?? "Waiting for chart data."}</p>
                </div>
                <div className="flex flex-wrap gap-2">
                  {currentChartPanels.map((panel) => (
                    <ChartToggle
                      key={panel.key}
                      label={panel.label}
                      active={panel.key === currentChartPanel?.key}
                      onClick={() => setActiveChart(panel.key)}
                    />
                  ))}
                </div>
              </div>

              <div className="mt-4 grid gap-3 sm:grid-cols-3">
                {appMode === "simulation" ? (
                  <>
                    <StatTile label="Best route" value={decision?.agent.selected_channel ?? "..."} accent="text-cyan-100" />
                    <StatTile
                      label="Live drift"
                      value={`${interferenceDelta >= 0 ? "+" : ""}${formatNumber(interferenceDelta)}%`}
                      accent={interferenceDelta > 0 ? "text-amber-100" : "text-emerald-100"}
                    />
                    <StatTile
                      label="Stream phase"
                      value={(streamEvent?.phase || "stable").replace(/^\w/, (match) => match.toUpperCase())}
                      accent="text-violet-100"
                    />
                  </>
                ) : (
                  <>
                    <StatTile label="Devices" value={String(networkFrame?.metrics.device_count ?? 0)} accent="text-cyan-100" />
                    <StatTile
                      label="Throughput"
                      value={networkFrame ? `${formatNumber(networkFrame.metrics.throughput_mbps)} Mbps` : "n/a"}
                      accent="text-emerald-100"
                    />
                    <StatTile
                      label="Avg latency"
                      value={networkFrame ? `${formatNumber(networkFrame.metrics.avg_latency_ms)} ms` : "0.0 ms"}
                      accent="text-violet-100"
                    />
                  </>
                )}
              </div>

              <div className="mt-4">
                {currentChartPanel ? <ChartPanel {...currentChartPanel} compact /> : <ChartPanel title="Analytics" subtitle="Waiting for data." labels={[]} datasets={[]} yTitle="Value" compact />}
              </div>
            </div>
          </div>

          <div className="mt-4 hidden gap-4 2xl:grid 2xl:grid-cols-3">
            {currentChartPanels.map((panel) => (
              <ChartPanel
                key={panel.key}
                title={panel.title}
                subtitle={panel.subtitle}
                labels={panel.labels}
                datasets={panel.datasets}
                yTitle={panel.yTitle}
                maxY={panel.maxY}
              />
            ))}
          </div>
        </section>
      </main>
    </div>
  );
}
