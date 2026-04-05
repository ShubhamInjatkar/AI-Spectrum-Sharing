from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import mean, pstdev
from threading import Lock
from typing import Any

from fastapi import WebSocket
from pydantic import BaseModel, Field

from .dqn_agent import LiveTelemetryDQN

DEVICE_HISTORY_LIMIT = 24
NETWORK_HISTORY_LIMIT = 32
DEVICE_STALE_AFTER_SECONDS = 30


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def average(values: list[float]) -> float:
    return mean(values) if values else 0.0


def spread(values: list[float]) -> float:
    return pstdev(values) if len(values) > 1 else 0.0


def mean_delta(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    return mean(abs(values[index] - values[index - 1]) for index in range(1, len(values)))


def now_utc() -> datetime:
    return datetime.now(UTC)


class DeviceTelemetryPayload(BaseModel):
    device_id: str = Field(min_length=1, max_length=80)
    latency_ms: float = Field(ge=0, le=5_000)
    throughput_mbps: float | None = Field(default=None, ge=0, le=100_000)
    jitter_ms: float | None = Field(default=None, ge=0, le=5_000)
    measured_at: datetime | None = None


class LiveNetworkStore:
    def __init__(self) -> None:
        self._devices: dict[str, deque[dict[str, Any]]] = {}
        self._history: deque[dict[str, Any]] = deque(maxlen=NETWORK_HISTORY_LIMIT)
        self._lock = Lock()
        self._learner = LiveTelemetryDQN(Path(__file__).with_name("live_network_dqn.pt"))

    def ingest(self, payload: DeviceTelemetryPayload) -> dict[str, Any]:
        sample_time = payload.measured_at.astimezone(UTC) if payload.measured_at else now_utc()
        sample = {
            "latency_ms": round(float(payload.latency_ms), 2),
            "throughput_mbps": (
                round(float(payload.throughput_mbps), 2) if payload.throughput_mbps is not None else None
            ),
            "jitter_ms": round(float(payload.jitter_ms), 2) if payload.jitter_ms is not None else None,
            "measured_at": sample_time,
        }

        with self._lock:
            history = self._devices.setdefault(payload.device_id, deque(maxlen=DEVICE_HISTORY_LIMIT))
            history.append(sample)
            return self._build_frame_locked(sample_time, append_history=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._build_frame_locked(now_utc(), append_history=False)

    def save_model(self) -> dict[str, Any]:
        with self._lock:
            return self._learner.save()

    def load_model(self) -> dict[str, Any]:
        with self._lock:
            loaded = self._learner.load()
            snapshot = self._build_frame_locked(now_utc(), append_history=False)
        return {
            "loaded": loaded,
            "training": snapshot.get("training", {}),
        }

    def _cleanup_locked(self, current_time: datetime) -> None:
        stale_before = current_time - timedelta(seconds=DEVICE_STALE_AFTER_SECONDS)
        for device_id in list(self._devices):
            history = self._devices[device_id]
            if not history or history[-1]["measured_at"] < stale_before:
                self._devices.pop(device_id, None)

    def _build_device_view(self, device_id: str, history: deque[dict[str, Any]], current_time: datetime) -> dict[str, Any]:
        samples = list(history)
        latest = samples[-1]
        latencies = [sample["latency_ms"] for sample in samples]
        throughputs = [sample["throughput_mbps"] for sample in samples if sample["throughput_mbps"] is not None]
        jitters = [sample["jitter_ms"] for sample in samples if sample["jitter_ms"] is not None]
        avg_latency_ms = average(latencies)
        avg_throughput_mbps = average(throughputs) if throughputs else None
        avg_jitter_ms = average(jitters) if jitters else mean_delta(latencies)
        latency_spread_ms = spread(latencies)
        latency_drift_ms = mean_delta(latencies)

        throughput_score = clamp(((avg_throughput_mbps or 0.0) / 160.0) * 24.0, 0, 24)
        latency_penalty = clamp((avg_latency_ms / 180.0) * 28.0, 0, 28)
        jitter_penalty = clamp((avg_jitter_ms / 55.0) * 18.0, 0, 18)

        computed_load = clamp(
            (avg_latency_ms / 170.0) * 58.0
            + (avg_jitter_ms / 65.0) * 18.0
            + (latency_drift_ms / 40.0) * 12.0
            + (latency_spread_ms / 45.0) * 12.0,
            0,
            100,
        )
        computed_interference = clamp(
            (latency_spread_ms / 35.0) * 66.0 + (avg_jitter_ms / 65.0) * 24.0 + (latency_drift_ms / 40.0) * 10.0,
            0,
            100,
        )
        computed_noise = clamp(
            (latency_drift_ms / 45.0) * 70.0 + (avg_jitter_ms / 80.0) * 22.0 + (latency_spread_ms / 55.0) * 8.0,
            0,
            100,
        )
        performance_score = clamp(
            68.0
            + throughput_score
            - latency_penalty
            - jitter_penalty
            - computed_load * 0.18
            - computed_interference * 0.16
            - computed_noise * 0.1,
            8,
            96,
        )

        if performance_score <= 34 or computed_interference >= 58 or computed_load >= 78 or computed_noise >= 48:
            status = "Critical"
        elif performance_score <= 64 or computed_interference >= 32 or computed_load >= 52 or computed_noise >= 24:
            status = "Warning"
        else:
            status = "Normal"

        return {
            "device_id": device_id,
            "latency_ms": round(latest["latency_ms"], 1),
            "throughput_mbps": (
                round(latest["throughput_mbps"], 1) if latest["throughput_mbps"] is not None else None
            ),
            "jitter_ms": round(latest["jitter_ms"], 1) if latest["jitter_ms"] is not None else round(avg_jitter_ms, 1),
            "avg_latency_ms": round(avg_latency_ms, 1),
            "avg_throughput_mbps": round(avg_throughput_mbps, 1) if avg_throughput_mbps is not None else None,
            "avg_jitter_ms": round(avg_jitter_ms, 1),
            "latency_spread_ms": round(latency_spread_ms, 1),
            "latency_drift_ms": round(latency_drift_ms, 1),
            "computed_load": round(computed_load, 1),
            "computed_interference": round(computed_interference, 1),
            "computed_noise": round(computed_noise, 1),
            "performance_score": round(performance_score, 1),
            "sample_count": len(samples),
            "status": status,
        }

    def _build_metrics(self, devices: list[dict[str, Any]]) -> dict[str, Any]:
        device_count = len(devices)
        if not device_count:
            return {
                "device_count": 0,
                "occupancy": 0.0,
                "avg_latency_ms": 0.0,
                "throughput_mbps": 0.0,
                "avg_jitter_ms": 0.0,
                "load": 0.0,
                "interference": 0.0,
                "noise": 0.0,
                "score": 0.0,
            }

        avg_latency_ms = average([device["avg_latency_ms"] for device in devices])
        avg_jitter_ms = average([device["avg_jitter_ms"] for device in devices])
        throughput_values = [device["avg_throughput_mbps"] for device in devices if device["avg_throughput_mbps"] is not None]
        throughput_mbps = average(throughput_values) if throughput_values else 0.0
        occupancy = clamp((device_count / 12.0) * 100.0, 0, 100)
        load = clamp(average([device["computed_load"] for device in devices]) * 0.72 + occupancy * 0.28, 0, 100)
        latency_spread_ms = spread([device["avg_latency_ms"] for device in devices])
        jitter_spread_ms = spread([device["avg_jitter_ms"] for device in devices])
        interference = clamp(
            average([device["computed_interference"] for device in devices]) * 0.65
            + (latency_spread_ms / 35.0) * 20.0
            + (avg_jitter_ms / 70.0) * 15.0,
            0,
            100,
        )
        noise = clamp(
            average([device["latency_drift_ms"] for device in devices]) * 1.15
            + jitter_spread_ms * 1.6
            + average([device["computed_noise"] for device in devices]) * 0.18,
            0,
            100,
        )
        score = clamp(
            average([device["performance_score"] for device in devices]) - interference * 0.14 - noise * 0.1,
            5,
            96,
        )

        return {
            "device_count": device_count,
            "occupancy": round(occupancy, 1),
            "avg_latency_ms": round(avg_latency_ms, 1),
            "throughput_mbps": round(throughput_mbps, 1),
            "avg_jitter_ms": round(avg_jitter_ms, 1),
            "load": round(load, 1),
            "interference": round(interference, 1),
            "noise": round(noise, 1),
            "score": round(score, 1),
        }

    def _build_ai(
        self,
        devices: list[dict[str, Any]],
        metrics: dict[str, Any],
        training: dict[str, Any],
    ) -> dict[str, Any]:
        if not devices:
            return {
                "name": "Live Decision Engine",
                "mode": "waiting for telemetry",
                "telemetry_mode": True,
                "status": "idle",
                "anchor_device": "No devices",
                "congested_device": "No devices",
                "selected_channel": "No devices",
                "backup_channel": "No backup",
                "confidence": 0.0,
                "reason_points": [
                    {"label": "Performance score", "value": "n/a"},
                    {"label": "Latency", "value": "n/a"},
                    {"label": "Throughput", "value": "n/a"},
                ],
                "action": {
                    "mode": "hold",
                    "count": 0,
                    "from_channel": "No congestion",
                    "to_channel": "Await telemetry",
                },
                "action_recommendation": "Waiting for real devices to publish telemetry.",
                "action_text": "Waiting for real devices to publish telemetry.",
                "summary": "No live device data has been received yet.",
                "allocations": [],
                "ranking": [],
            }

        decision_updates = training.get("policy_updates", {})
        dqn_enabled = bool(training.get("enabled") and decision_updates)
        ranked_devices = sorted(
            devices,
            key=lambda device: (-device["performance_score"], device["avg_latency_ms"], device["device_id"]),
        )

        if dqn_enabled:
            anchor_candidates = sorted(
                devices,
                key=lambda device: (
                    -(
                        decision_updates.get(device["device_id"], {}).get("q_values", {}).get("promote_anchor", 0.0) * 12.0
                        + device["performance_score"] * 0.35
                        - device["avg_latency_ms"] * 0.05
                    ),
                    device["avg_latency_ms"],
                ),
            )
            anchor = anchor_candidates[0]

            congested_candidates = sorted(
                devices,
                key=lambda device: (
                    -(
                        decision_updates.get(device["device_id"], {}).get("q_values", {}).get("deprioritize", 0.0) * 12.0
                        + device["computed_interference"] * 0.25
                        + device["computed_load"] * 0.18
                        - device["performance_score"] * 0.15
                    ),
                    -device["computed_interference"],
                ),
            )
            congested = congested_candidates[0]
            if congested["device_id"] == anchor["device_id"] and len(congested_candidates) > 1:
                congested = congested_candidates[1]

            anchor_decision = decision_updates.get(anchor["device_id"], {})
            congested_decision = decision_updates.get(congested["device_id"], {})
            confidence = round(
                clamp(
                    average(
                        [
                            anchor_decision.get("confidence", 60.0),
                            congested_decision.get("confidence", anchor_decision.get("confidence", 60.0)),
                        ]
                    ),
                    55.0,
                    97.0,
                ),
                1,
            )
            active_mode = (
                congested_decision.get("recommended_action") == "deprioritize"
                or anchor_decision.get("recommended_action") == "promote_anchor"
                or metrics["interference"] >= 36
                or metrics["load"] >= 62
            )
            decision_source = "adaptive response"
            reason_points = [
                {
                    "label": "Anchor score",
                    "value": f"{anchor['performance_score']}%",
                },
                {
                    "label": "Backup score",
                    "value": f"{(ranked_devices[1] if len(ranked_devices) > 1 else anchor)['performance_score']}%",
                },
                {
                    "label": "Anchor latency",
                    "value": f"{anchor['avg_latency_ms']} ms",
                },
                {
                    "label": "Observed pressure",
                    "value": f"{congested['computed_interference']}%",
                },
            ]
        else:
            anchor = ranked_devices[0]
            second_best = ranked_devices[1] if len(ranked_devices) > 1 else anchor
            congested = sorted(
                devices,
                key=lambda device: (device["performance_score"], -device["computed_load"], device["device_id"]),
            )[0]
            score_gap = max(0.0, anchor["performance_score"] - second_best["performance_score"])
            confidence = round(
                clamp(
                    55.0
                    + score_gap * 2.2
                    + max(0.0, 40.0 - metrics["interference"]) * 0.45
                    ,
                    52,
                    97,
                ),
                1,
            )
            anchor_decision = decision_updates.get(anchor["device_id"], {})
            congested_decision = decision_updates.get(congested["device_id"], {})
            active_mode = (
                congested["status"] != "Normal"
                or metrics["interference"] >= 40
                or metrics["noise"] >= 32
                or metrics["load"] >= 60
            )
            decision_source = "live ranking"
            reason_points = [
                {"label": "Performance score", "value": f"{anchor['performance_score']}%"},
                {"label": "Lowest latency", "value": f"{anchor['avg_latency_ms']} ms"},
                {
                    "label": "Available throughput",
                    "value": f"{anchor['avg_throughput_mbps'] if anchor['avg_throughput_mbps'] is not None else 0.0} Mbps",
                },
                {
                    "label": "Confidence gap",
                    "value": f"{round(score_gap, 1)}%",
                },
            ]
        recommendation_source = "DQN" if dqn_enabled else "Telemetry"
        action_recommendation = (
            f"Shift attention away from {congested['device_id']} while keeping {anchor['device_id']} as the primary route."
            if active_mode and anchor["device_id"] != congested["device_id"]
            else f"Hold {anchor['device_id']} as the primary route while live conditions stay stable."
        )
        allocations: list[dict[str, Any]] = [
            {
                "user": anchor["device_id"],
                "from_channel": anchor["device_id"],
                "to_channel": anchor["device_id"],
                "confidence": round(anchor_decision.get("confidence", confidence), 1),
                "gain": round(max(2.0, anchor["performance_score"] * 0.16), 1),
                "priority": "anchor",
            }
        ]

        if congested["device_id"] != anchor["device_id"]:
            allocations.append(
                {
                    "user": congested["device_id"],
                    "from_channel": congested["device_id"],
                    "to_channel": anchor["device_id"],
                    "confidence": round(
                        congested_decision.get(
                            "confidence",
                            clamp(confidence - congested["computed_interference"] * 0.1, 48, 95),
                        ),
                        1,
                    ),
                    "gain": round(max(2.0, (100 - congested["performance_score"]) * 0.12), 1),
                    "priority": "protect",
                }
            )

        ranking = [
            {
                "device_id": device["device_id"],
                "performance_score": device["performance_score"],
                "latency_ms": device["avg_latency_ms"],
                "status": device["status"],
            }
            for device in ranked_devices
        ]
        return {
            "name": "Live Decision Engine",
            "mode": decision_source,
            "telemetry_mode": True,
            "status": "active" if active_mode else "monitoring",
            "anchor_device": anchor["device_id"],
            "congested_device": congested["device_id"],
            "selected_channel": anchor["device_id"],
            "backup_channel": congested["device_id"],
            "confidence": confidence,
            "reason_points": reason_points,
            "action": {
                "mode": "reroute" if active_mode and congested["device_id"] != anchor["device_id"] else "hold",
                "count": len(allocations),
                "from_channel": congested["device_id"],
                "to_channel": anchor["device_id"],
            },
            "action_recommendation": action_recommendation,
            "action_text": action_recommendation,
            "summary": (
                f"{anchor['device_id']} is the current anchor while {congested['device_id']} is carrying the highest pressure in the live view."
            ),
            "allocations": allocations,
            "ranking": ranking,
        }

    def _build_event(self, metrics: dict[str, Any], agent: dict[str, Any]) -> dict[str, Any]:
        if metrics["device_count"] == 0:
            return {
                "level": "Normal",
                "title": "Waiting for live devices",
                "message": "No telemetry has been received yet.",
            }

        if metrics["interference"] >= 48 or metrics["noise"] >= 36 or metrics["load"] >= 72:
            return {
                "level": "Critical",
                "title": f"CRITICAL: {agent['congested_device']} is degrading live performance",
                "message": agent["action_recommendation"],
            }

        if metrics["interference"] >= 28 or metrics["noise"] >= 18 or metrics["load"] >= 48:
            return {
                "level": "Warning",
                "title": "WARNING: live telemetry is drifting upward",
                "message": f"{agent['anchor_device']} remains the best anchor while {agent['congested_device']} needs attention.",
            }

        return {
            "level": "Normal",
            "title": "Normal: live device telemetry is stable",
            "message": f"{agent['anchor_device']} currently has the strongest real-world telemetry profile.",
        }

    def _build_frame_locked(self, current_time: datetime, *, append_history: bool) -> dict[str, Any]:
        self._cleanup_locked(current_time)
        devices = [
            self._build_device_view(device_id, history, current_time)
            for device_id, history in self._devices.items()
            if history
        ]
        devices.sort(key=lambda device: (-device["performance_score"], device["computed_load"], device["device_id"]))
        metrics = self._build_metrics(devices)
        training = (
            self._learner.observe(devices, metrics)
            if append_history
            else self._learner.infer(devices, metrics)
        )
        agent = self._build_ai(devices, metrics, training)
        event = self._build_event(metrics, agent)

        history_point = {
            "tick": current_time.strftime("%H:%M:%S"),
            "avg_latency_ms": metrics["avg_latency_ms"],
            "throughput_mbps": metrics["throughput_mbps"],
            "load": metrics["load"],
            "interference": metrics["interference"],
            "noise": metrics["noise"],
            "score": metrics["score"],
        }
        if append_history and (not self._history or self._history[-1] != history_point):
            self._history.append(history_point)

        return {
            "mode": "live-network",
            "generated_at": current_time.isoformat(),
            "devices": devices,
            "metrics": metrics,
            "agent": agent,
            "training": training,
            "summary": agent["summary"],
            "event": event,
            "timeseries": list(self._history),
        }


LIVE_NETWORK_STORE = LiveNetworkStore()
NETWORK_WATCHERS: set[WebSocket] = set()


async def broadcast_network_frame(frame: dict[str, Any] | None = None) -> None:
    if not NETWORK_WATCHERS:
        return

    snapshot = frame or LIVE_NETWORK_STORE.snapshot()
    stale_watchers: list[WebSocket] = []
    for watcher in list(NETWORK_WATCHERS):
        try:
            await watcher.send_json(snapshot)
        except Exception:
            stale_watchers.append(watcher)

    for watcher in stale_watchers:
        NETWORK_WATCHERS.discard(watcher)


async def register_network_watcher(websocket: WebSocket) -> None:
    await websocket.accept()
    NETWORK_WATCHERS.add(websocket)
    await websocket.send_json(LIVE_NETWORK_STORE.snapshot())


def unregister_network_watcher(websocket: WebSocket) -> None:
    NETWORK_WATCHERS.discard(websocket)
