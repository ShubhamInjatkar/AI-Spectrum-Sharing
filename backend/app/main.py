from __future__ import annotations

import asyncio
from collections import Counter
import json
import math
import random
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .live_network import (
    DeviceTelemetryPayload,
    LIVE_NETWORK_STORE,
    broadcast_network_frame,
    register_network_watcher,
    unregister_network_watcher,
)
from .simulation_training import SimulationPolicyTrainer

CHANNEL_IDS = ("CH-01", "CH-02", "CH-03", "CH-04", "CH-05")
DEV_ORIGINS = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
DEFAULT_AGENT_PROFILE = {
    "load_weight": 0.54,
    "interference_weight": 0.72,
    "occupancy_weight": 6.3,
    "projected_weight": 11.4,
    "noise_weight": 0.14,
    "bandwidth_weight": 0.18,
    "demand_weight": 0.06,
    "critical_bias": 8.5,
    "high_bias": 4.5,
}
AGENT_PROFILE_VERSION = "2026-03-30-cache-v3"
AGENT_PROFILE_CACHE = Path(__file__).with_name("agent_profile_cache.json")
SIMULATION_POLICY_CACHE = Path(__file__).with_name("simulation_policy_cache.json")
COLLECTOR_DIR = Path(__file__).with_name("collector")
FRONTEND_DIST_DIR = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class SpectrumRequest(BaseModel):
    users: int = Field(default=8, ge=2, le=24)
    noise_level: int = Field(default=35, ge=0, le=100)
    bandwidth: int = Field(default=50, ge=10, le=100)
    seed: int | None = Field(default=None, ge=1, le=999_999_999)
    tick: int = Field(default=0, ge=0, le=99_999)


app = FastAPI(title="AI-Driven Spectrum Sharing", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=DEV_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/collector", StaticFiles(directory=str(COLLECTOR_DIR), html=True), name="collector")


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def clamp_int(value: int | float, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(round(value))))


def clamp_profile(value: float, minimum: float, maximum: float) -> float:
    return round(clamp(value, minimum, maximum), 4)


def make_session_seed(seed: int | None = None) -> int:
    return seed or random.randint(100_000, 999_999)


def active_channels_for(users: int) -> list[str]:
    return list(CHANNEL_IDS[: 5 if users >= 12 else 4])


def make_priority(demand: float) -> str:
    if demand >= 78:
        return "critical"
    if demand >= 55:
        return "high"
    return "standard"


def built_frontend_response(path: str = "") -> Response:
    index_path = FRONTEND_DIST_DIR / "index.html"
    if not index_path.exists():
        return Response(
            content=json.dumps(
                {
                    "name": "AI-Driven Spectrum Sharing API",
                    "status": "running",
                    "docs": "/docs",
                    "collector": "/collector/",
                    "frontend": "Build the frontend with `cd frontend && npm run build` to serve it from :8000.",
                }
            ),
            media_type="application/json",
        )

    requested_path = path.strip("/")
    if requested_path:
        candidate = (FRONTEND_DIST_DIR / requested_path).resolve()
        try:
            candidate.relative_to(FRONTEND_DIST_DIR.resolve())
        except ValueError:
            return FileResponse(index_path)

        if candidate.exists() and candidate.is_file():
            return FileResponse(candidate)

    return FileResponse(index_path)


def build_environment(payload: SpectrumRequest) -> dict[str, Any]:
    seed = payload.seed or random.randint(100_000, 999_999)
    rng = random.Random(seed)
    channels = active_channels_for(payload.users)
    occupancy = {channel_id: 0 for channel_id in channels}
    users: list[dict[str, Any]] = []
    phase = payload.tick / 2.6

    for index in range(payload.users):
        preferred_index = channels.index(rng.choice(channels))
        drift = 0
        if math.sin(phase + index * 0.85) > 0.8:
            drift = 1
        elif math.cos(phase + index * 0.65) < -0.82:
            drift = -1
        preferred_channel = channels[(preferred_index + drift) % len(channels)]
        demand = round(
            clamp(
                rng.uniform(28, 82)
                + payload.bandwidth * 0.17
                - payload.noise_level * 0.09
                + math.sin(index + payload.users / 3 + phase) * 5,
                18,
                100,
            ),
            1,
        )
        qos = round(
            clamp(
                100 - payload.noise_level * 0.42 + rng.uniform(-10, 10) + math.cos(phase + index / 4) * 2.5,
                26,
                98,
            ),
            1,
        )
        latency = round(
            clamp(
                rng.uniform(8, 18)
                + payload.noise_level * 0.09
                - payload.bandwidth * 0.03
                + math.sin(phase + index / 3) * 1.6,
                4,
                40,
            ),
            1,
        )
        priority = make_priority(demand)
        users.append(
            {
                "id": f"U-{index + 1:02d}",
                "preferred_channel": preferred_channel,
                "demand": demand,
                "qos": qos,
                "latency_ms": latency,
                "priority": priority,
            }
        )
        occupancy[preferred_channel] += 1

    channel_snapshots: list[dict[str, Any]] = []
    for position, channel_id in enumerate(channels):
        assigned_users = [user["id"] for user in users if user["preferred_channel"] == channel_id]
        occupancy_load = occupancy[channel_id] / max(1, payload.users)
        load = round(
            clamp(
                18
                + occupancy_load * 48
                + occupancy[channel_id] * 5.6
                + payload.users * 0.42
                + payload.noise_level * 0.08
                - payload.bandwidth * 0.09
                + math.sin(phase + position) * 5.1
                + rng.uniform(-4.5, 4.5),
                14,
                91,
            ),
            1,
        )
        interference = round(
            clamp(
                7
                + load * 0.34
                + occupancy[channel_id] * 4.6
                + payload.noise_level * 0.16
                - payload.bandwidth * 0.08
                + math.cos(phase + position / 1.7) * 3.1
                + rng.uniform(-4.5, 4.5),
                6,
                84,
            ),
            1,
        )
        quality = round(
            clamp(
                100 - interference * 0.68 - occupancy[channel_id] * 1.8 + rng.uniform(-4, 6),
                18,
                99,
            ),
            1,
        )
        predicted_demand = round(
            clamp(
                load * 0.8
                + occupancy[channel_id] * 3.8
                + math.sin(position + payload.users / 2 + phase) * 6.4
                + rng.uniform(-3.5, 3.5),
                8,
                92,
            ),
            1,
        )
        channel_snapshots.append(
            {
                "id": channel_id,
                "load": load,
                "interference": interference,
                "quality": quality,
                "predicted_demand": predicted_demand,
                "users": assigned_users,
                "user_count": len(assigned_users),
            }
        )

    avg_load = mean(channel["load"] for channel in channel_snapshots)
    avg_interference = mean(channel["interference"] for channel in channel_snapshots)
    efficiency = round(
        clamp(
            95
            - avg_interference * 0.5
            - payload.users * 0.95
            + payload.bandwidth * 0.24
            + rng.uniform(-1.8, 1.8),
            32,
            98,
        ),
        1,
    )
    throughput = round(
        payload.bandwidth * payload.users * (efficiency / 100) * 1.58,
        1,
    )
    fairness = round(
        clamp(
            100 - (max(channel["load"] for channel in channel_snapshots) - min(channel["load"] for channel in channel_snapshots)) * 0.82,
            34,
            98,
        ),
        1,
    )

    timeseries: list[dict[str, Any]] = []
    for tick in range(20):
        live_tick = tick + payload.tick
        wave = math.sin((live_tick + phase) / 2.8) * 6 + math.cos((live_tick + phase) / 4.4) * 3.5
        usage = round(clamp(avg_load + wave + rng.uniform(-2.4, 2.4), 8, 98), 1)
        interference = round(
            clamp(avg_interference + math.cos((live_tick + phase) / 3.2) * 4.8 + rng.uniform(-2, 2), 3, 96),
            1,
        )
        channel_points = {}
        for position, channel in enumerate(channel_snapshots):
            trend = (
                math.sin((live_tick + position + phase) / 2.2) * 5.4
                + math.cos((live_tick + position + phase) / 4.1) * 2.6
            )
            channel_points[channel["id"]] = round(
                clamp(channel["load"] + trend + rng.uniform(-2, 2), 5, 98),
                1,
            )

        timeseries.append(
            {
                "tick": f"T{live_tick + 1:02d}",
                "usage": usage,
                "interference": interference,
                "channels": channel_points,
            }
        )

    return {
        "seed": seed,
        "generated_at": datetime.now(UTC).isoformat(),
        "config": {
            "users": payload.users,
            "noise_level": payload.noise_level,
            "bandwidth": payload.bandwidth,
            "channel_count": len(channels),
            "tick": payload.tick,
        },
        "channels": channel_snapshots,
        "users": users,
        "summary": {
            "crowding_index": round(clamp(avg_load + avg_interference * 0.25, 10, 100), 1),
            "active_devices": payload.users,
            "avg_channel_quality": round(mean(channel["quality"] for channel in channel_snapshots), 1),
        },
        "metrics": {
            "efficiency": efficiency,
            "interference": round(avg_interference, 1),
            "throughput": throughput,
            "fairness": fairness,
        },
        "timeseries": timeseries,
    }


def evaluate_profile(result: dict[str, Any]) -> float:
    optimized_channels = result["optimized_channels"]
    avg_headroom = mean(channel["headroom"] for channel in optimized_channels)
    load_spread = max(channel["optimized_load"] for channel in optimized_channels) - min(
        channel["optimized_load"] for channel in optimized_channels
    )
    return (
        result["metrics"]["efficiency"] * 1.35
        - result["metrics"]["optimized_interference"] * 1.1
        + result["metrics"]["interference_reduction"] * 0.45
        + avg_headroom * 0.4
        - load_spread * 0.12
    )


def score_simulation_decision(result: dict[str, Any]) -> float:
    return (
        evaluate_profile(result)
        + result["metrics"]["interference_reduction"] * 0.38
        + result["metrics"]["efficiency"] * 0.18
        + result["agent"]["confidence"] * 0.12
        + result["metrics"]["throughput"] * 0.01
    )


def mutate_profile(base_profile: dict[str, float], rng: random.Random, intensity: float) -> dict[str, float]:
    return {
        "load_weight": clamp_profile(base_profile["load_weight"] + rng.uniform(-0.16, 0.16) * intensity, 0.22, 1.18),
        "interference_weight": clamp_profile(
            base_profile["interference_weight"] + rng.uniform(-0.2, 0.2) * intensity,
            0.3,
            1.32,
        ),
        "occupancy_weight": clamp_profile(
            base_profile["occupancy_weight"] + rng.uniform(-2.4, 2.4) * intensity,
            2.2,
            12.8,
        ),
        "projected_weight": clamp_profile(
            base_profile["projected_weight"] + rng.uniform(-2.1, 2.1) * intensity,
            5.8,
            18.5,
        ),
        "noise_weight": clamp_profile(base_profile["noise_weight"] + rng.uniform(-0.05, 0.05) * intensity, 0.03, 0.4),
        "bandwidth_weight": clamp_profile(
            base_profile["bandwidth_weight"] + rng.uniform(-0.05, 0.05) * intensity,
            0.03,
            0.45,
        ),
        "demand_weight": clamp_profile(base_profile["demand_weight"] + rng.uniform(-0.03, 0.03) * intensity, 0.01, 0.15),
        "critical_bias": clamp_profile(base_profile["critical_bias"] + rng.uniform(-2.4, 2.4) * intensity, 3.5, 16.0),
        "high_bias": clamp_profile(base_profile["high_bias"] + rng.uniform(-1.6, 1.6) * intensity, 0.8, 9.5),
    }


def load_cached_agent_profile() -> dict[str, Any] | None:
    if not AGENT_PROFILE_CACHE.exists():
        return None

    try:
        cached = json.loads(AGENT_PROFILE_CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    if cached.get("version") != AGENT_PROFILE_VERSION or "weights" not in cached:
        return None

    return cached


def save_cached_agent_profile(profile: dict[str, Any]) -> None:
    try:
        AGENT_PROFILE_CACHE.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    except OSError:
        return


def optimize_environment(
    environment: dict[str, Any],
    payload: SpectrumRequest,
    profile: dict[str, float] | None = None,
) -> dict[str, Any]:
    active_profile = profile or TRAINED_AGENT_PROFILE["weights"]
    rng = random.Random(environment["seed"] + 17)
    channels = environment["channels"]
    users = environment["users"]
    base_pressure = {
        channel["id"]: (
            channel["load"] * active_profile["load_weight"]
            + channel["interference"] * active_profile["interference_weight"]
            + channel["user_count"] * active_profile["occupancy_weight"]
        )
        for channel in channels
    }
    projected_counts = {channel["id"]: 0 for channel in channels}
    allocations: list[dict[str, Any]] = []

    for user in sorted(users, key=lambda item: item["demand"], reverse=True):
        priority_bias = {
            "critical": active_profile["critical_bias"],
            "high": active_profile["high_bias"],
            "standard": 0,
        }.get(user["priority"], 0)
        candidate_scores: list[dict[str, Any]] = []
        for channel in channels:
            channel_id = channel["id"]
            score = (
                base_pressure[channel_id]
                + projected_counts[channel_id] * active_profile["projected_weight"]
                + payload.noise_level * active_profile["noise_weight"]
                - payload.bandwidth * active_profile["bandwidth_weight"]
                - user["demand"] * active_profile["demand_weight"]
                - priority_bias
            )
            candidate_scores.append(
                {
                    "channel": channel_id,
                    "score": score,
                    "predicted_interference": clamp(
                        channel["interference"] * 0.72 + projected_counts[channel_id] * 4.1,
                        2,
                        90,
                    ),
                }
            )

        ranked_options = sorted(candidate_scores, key=lambda candidate: candidate["score"])
        best_option = ranked_options[0]
        second_option = ranked_options[1] if len(ranked_options) > 1 else ranked_options[0]
        margin = max(0.1, second_option["score"] - best_option["score"])
        current_pressure = (
            base_pressure[user["preferred_channel"]]
            + payload.noise_level * active_profile["noise_weight"]
            - payload.bandwidth * active_profile["bandwidth_weight"]
        )
        confidence = round(
            clamp(
                61
                + margin * 4.6
                + (100 - best_option["predicted_interference"]) * 0.11
                + rng.uniform(-1.4, 1.4),
                58,
                99,
            ),
            1,
        )
        projected_counts[best_option["channel"]] += 1
        gain = round(clamp(current_pressure - best_option["score"] + 6, 1, 42), 1)
        allocations.append(
            {
                "user": user["id"],
                "from_channel": user["preferred_channel"],
                "to_channel": best_option["channel"],
                "confidence": confidence,
                "gain": gain,
                "priority": user["priority"],
            }
        )

    user_lookup = {user["id"]: user for user in users}
    assigned_by_channel = {
        channel["id"]: [allocation for allocation in allocations if allocation["to_channel"] == channel["id"]]
        for channel in channels
    }
    optimized_channels: list[dict[str, Any]] = []
    for index, channel in enumerate(channels):
        channel_id = channel["id"]
        channel_allocations = assigned_by_channel[channel_id]
        assigned_count = len(channel_allocations)
        assigned_demands = [user_lookup[item["user"]]["demand"] for item in channel_allocations]
        demand_profile = mean(assigned_demands) if assigned_demands else channel["predicted_demand"] * 0.76
        priority_pressure = sum(
            1.35 if item["priority"] == "critical" else 0.75 if item["priority"] == "high" else 0.3
            for item in channel_allocations
        )
        stability = clamp(channel["quality"] - channel["interference"] * 0.34 + math.sin(payload.tick + index) * 4.2, 4, 96)
        optimized_load = round(
            clamp(
                channel["load"] * 0.58
                + assigned_count * 3.2
                + demand_profile * 0.15
                + priority_pressure * 0.8
                - payload.bandwidth * 0.05
                + math.cos(payload.tick / 2 + index) * 1.4,
                5,
                89,
            ),
            1,
        )
        optimized_interference = round(
            clamp(
                channel["interference"] * 0.5
                + assigned_count * 2.1
                + payload.noise_level * 0.08
                + demand_profile * 0.1
                + priority_pressure * 1.1
                - stability * 0.1
                - payload.bandwidth * 0.05
                + math.sin(payload.tick / 2.2 + index / 1.5) * 1.6,
                2,
                78,
            ),
            1,
        )
        headroom = round(
            clamp(
                100 - optimized_load * 0.74 - optimized_interference * 0.42 + stability * 0.16 - assigned_count * 0.45,
                6,
                90,
            ),
            1,
        )
        optimized_channels.append(
            {
                "id": channel_id,
                "assigned_users": assigned_count,
                "optimized_load": optimized_load,
                "optimized_interference": optimized_interference,
                "headroom": headroom,
                "stability": round(stability, 1),
            }
        )

    baseline_interference = environment["metrics"]["interference"]
    optimized_interference = round(mean(channel["optimized_interference"] for channel in optimized_channels), 1)
    interference_reduction = round(
        max(
            0.0,
            ((baseline_interference - optimized_interference) / max(baseline_interference, 1)) * 100,
        ),
        1,
    )
    optimized_efficiency = round(
        clamp(
            environment["metrics"]["efficiency"]
            + (baseline_interference - optimized_interference) * 0.74
            + 8,
            environment["metrics"]["efficiency"] + 1,
            99,
        ),
        1,
    )
    optimized_throughput = round(environment["metrics"]["throughput"] * (1 + interference_reduction / 120), 1)
    ranked_channels = sorted(
        [
            {
                "id": channel["id"],
                "score": round(clamp(100 - (base_pressure[channel["id"]] * 0.72), 5, 98), 1),
                "predicted_interference": next(
                    item["optimized_interference"]
                    for item in optimized_channels
                    if item["id"] == channel["id"]
                ),
                "headroom": next(item["headroom"] for item in optimized_channels if item["id"] == channel["id"]),
            }
            for channel in channels
        ],
        key=lambda item: item["score"],
        reverse=True,
    )
    best_channel = ranked_channels[0]
    backup_channel = ranked_channels[1] if len(ranked_channels) > 1 else ranked_channels[0]
    score_gap = max(0.1, best_channel["score"] - backup_channel["score"])
    best_route = Counter(
        (item["from_channel"], item["to_channel"])
        for item in allocations
        if item["from_channel"] != item["to_channel"]
    ).most_common(1)
    action_route = best_route[0] if best_route else ((best_channel["id"], best_channel["id"]), 0)
    action_count = action_route[1]
    channel_trend_points = [point["channels"][best_channel["id"]] for point in environment["timeseries"][-5:]]
    trend_delta = channel_trend_points[-1] - channel_trend_points[0]
    if trend_delta <= -3.5:
        trend_label = "easing"
    elif trend_delta >= 3.5:
        trend_label = "rising"
    else:
        trend_label = "stable"
    agent_confidence = round(
        clamp(
            55
            + min(score_gap, 12) * 2.2
            + best_channel["headroom"] * 0.04
            - best_channel["predicted_interference"] * 0.03,
            58,
            95,
        ),
        1,
    )
    agent = {
        "name": "Spectrum Pilot",
        "mode": "adaptive scoring agent",
        "selected_channel": best_channel["id"],
        "backup_channel": backup_channel["id"],
        "confidence": agent_confidence,
        "reason_points": [
            {"label": "Lowest interference", "value": f"{best_channel['predicted_interference']}%"},
            {"label": "Highest headroom", "value": f"{best_channel['headroom']}%"},
            {"label": "Score gap", "value": f"{score_gap:.1f} pts"},
        ],
        "action": {
            "count": action_count,
            "from_channel": action_route[0][0],
            "to_channel": action_route[0][1],
            "mode": "reroute" if action_count > 0 else "hold",
        },
        "reasoning": [
            f"Primary target {best_channel['id']} has the strongest channel score at {best_channel['score']} points.",
            f"Predicted interference on {best_channel['id']} is only {best_channel['predicted_interference']}% with {best_channel['headroom']}% headroom available.",
            f"The decision margin over {backup_channel['id']} is {score_gap:.1f} points, which drives the confidence estimate.",
            f"Fallback route {backup_channel['id']} stays available if load spikes or the noise floor shifts.",
            f"Current decision projects {interference_reduction}% lower interference and {optimized_efficiency}% efficiency after reallocation.",
        ],
    }
    if action_count > 0:
        agent["action_text"] = (
            f"Reassign {action_count} users from {action_route[0][0]} to {action_route[0][1]} to cut interference."
        )
    else:
        agent["action_text"] = (
            f"Hold current routing and keep {best_channel['id']} primary with {backup_channel['id']} on standby."
        )
    return {
        "seed": environment["seed"],
        "model": "Spectrum Pilot Heuristic",
        "decision_latency_ms": rng.randint(420, 980),
        "summary": (
            "AI shifts high-demand devices toward lower-pressure channels, balancing load and preserving "
            "headroom in noisy conditions."
        ),
        "agent": agent,
        "allocations": allocations,
        "channel_ranking": ranked_channels,
        "optimized_channels": optimized_channels,
        "metrics": {
            "efficiency": optimized_efficiency,
            "interference_reduction": interference_reduction,
            "throughput": optimized_throughput,
            "baseline_efficiency": environment["metrics"]["efficiency"],
            "baseline_interference": baseline_interference,
            "optimized_interference": optimized_interference,
        },
    }


def sample_training_payload(rng: random.Random, episode: int) -> SpectrumRequest:
    scenario_offset = episode % 8
    users = clamp_int(rng.randint(4, 24) + (2 if scenario_offset in {1, 4, 7} else 0), 4, 24)
    noise_level = clamp_int(rng.randint(10, 92) + (12 if scenario_offset in {2, 5, 6} else 0), 0, 100)
    bandwidth = rng.choice([20, 30, 40, 50, 60, 80, 100])
    if scenario_offset == 3:
        bandwidth = max(20, bandwidth - 20)
    elif scenario_offset == 6:
        bandwidth = clamp_int(bandwidth + 20, 20, 100)
    elif scenario_offset == 7:
        bandwidth = max(20, bandwidth - 10)

    return SpectrumRequest(
        users=users,
        noise_level=noise_level,
        bandwidth=bandwidth,
        seed=730_000 + episode * 97 + rng.randint(0, 60),
        tick=(episode * 5) % 21,
    )


def evaluate_training_profile(profile: dict[str, float], payloads: list[SpectrumRequest]) -> float:
    total_score = 0.0
    for payload in payloads:
        environment = build_environment(payload)
        result = optimize_environment(environment, payload, profile=profile)
        event_bias = 1.0 if payload.noise_level >= 60 else 0.6
        trigger_bonus = result["metrics"]["interference_reduction"] * event_bias
        confidence_bonus = result["agent"]["confidence"] * 0.18
        throughput_bonus = result["metrics"]["throughput"] * 0.015
        resilience_penalty = max(0.0, 18 - mean(channel["headroom"] for channel in result["optimized_channels"])) * 0.9
        stability_bonus = mean(channel.get("stability", 50) for channel in result["optimized_channels"]) * 0.08
        total_score += (
            evaluate_profile(result)
            + trigger_bonus
            + confidence_bonus
            + throughput_bonus
            + stability_bonus
            - resilience_penalty
        )

    return total_score / len(payloads)


def score_profile_bundle(
    profile: dict[str, float],
    train_payloads: list[SpectrumRequest],
    validation_payloads: list[SpectrumRequest],
) -> float:
    train_score = evaluate_training_profile(profile, train_payloads)
    validation_score = evaluate_training_profile(profile, validation_payloads)
    return train_score * 0.72 + validation_score * 0.28


def train_agent_profile() -> dict[str, Any]:
    rng = random.Random(20_260_330)
    train_episodes = [sample_training_payload(rng, episode) for episode in range(96)]
    validation_episodes = [sample_training_payload(rng, 96 + episode) for episode in range(32)]
    best_profile = DEFAULT_AGENT_PROFILE.copy()
    best_score = score_profile_bundle(best_profile, train_episodes, validation_episodes)
    rounds = 192

    for round_index in range(rounds):
        source = best_profile if round_index % 3 else DEFAULT_AGENT_PROFILE
        intensity = max(0.1, 1 - round_index / (rounds + 26))
        candidate_profile = mutate_profile(source, rng, intensity)
        candidate_score = score_profile_bundle(
            candidate_profile,
            train_episodes,
            validation_episodes,
        )
        if candidate_score > best_score:
            best_profile = candidate_profile
            best_score = candidate_score

    for fine_tune_round in range(40):
        candidate_profile = mutate_profile(best_profile, rng, max(0.03, 0.16 - fine_tune_round * 0.003))
        candidate_score = score_profile_bundle(
            candidate_profile,
            train_episodes,
            validation_episodes,
        )
        if candidate_score > best_score:
            best_profile = candidate_profile
            best_score = candidate_score

    return {
        "weights": best_profile,
        "version": AGENT_PROFILE_VERSION,
        "episodes": len(train_episodes) + len(validation_episodes),
        "score": round(best_score, 1),
    }


def load_or_train_agent_profile() -> dict[str, Any]:
    cached_profile = load_cached_agent_profile()
    if cached_profile is not None:
        return cached_profile

    trained_profile = train_agent_profile()
    save_cached_agent_profile(trained_profile)
    return trained_profile


TRAINED_AGENT_PROFILE = load_or_train_agent_profile()
SIMULATION_POLICY = SimulationPolicyTrainer(
    cache_path=SIMULATION_POLICY_CACHE,
    version=f"{AGENT_PROFILE_VERSION}-adaptive-v1",
    base_profile=TRAINED_AGENT_PROFILE["weights"],
)


def build_live_request(base_payload: SpectrumRequest, session_seed: int, tick: int) -> tuple[SpectrumRequest, dict[str, Any]]:
    seed = make_session_seed(base_payload.seed or session_seed)
    rng = random.Random(seed + tick * 131)
    noise_shift = rng.randint(-2, 2)
    bandwidth_shift = rng.choice([-5, 0, 5]) if rng.random() < 0.4 else 0
    user_shift = 0
    phase = "stable"

    if tick > 0 and (seed + tick) % 23 == 0:
        phase = "critical"
        noise_shift += rng.randint(16, 24)
        bandwidth_shift -= rng.choice([5, 10])
        user_shift = 1
    elif tick > 0 and (seed + tick) % 7 == 0:
        phase = "spike"
        noise_shift += rng.randint(9, 15)
        bandwidth_shift -= 5
    elif tick > 0 and ((seed + tick - 1) % 23 == 0 or (seed + tick - 1) % 7 == 0):
        phase = "stabilizing"
        noise_shift -= rng.randint(4, 9)
        bandwidth_shift += rng.choice([5, 10])

    live_payload = SpectrumRequest(
        users=clamp_int(base_payload.users + user_shift, 2, 24),
        noise_level=clamp_int(base_payload.noise_level + noise_shift, 0, 100),
        bandwidth=clamp_int(base_payload.bandwidth + bandwidth_shift, 10, 100),
        seed=seed,
        tick=tick,
    )

    return live_payload, {
        "phase": phase,
        "noise_shift": noise_shift,
        "bandwidth_shift": bandwidth_shift,
        "user_shift": user_shift,
    }


def build_live_event(
    environment: dict[str, Any],
    decision: dict[str, Any],
    story: dict[str, Any],
) -> dict[str, Any]:
    busiest_channel = max(environment["channels"], key=lambda channel: channel["interference"])
    baseline_interference = environment["metrics"]["interference"]
    config = environment["config"]
    overload_mode = config["users"] >= 16 and config["noise_level"] >= 65
    trigger_ai = (
        story["phase"] in {"spike", "critical"}
        or baseline_interference >= 40
        or busiest_channel["interference"] >= 45
        or overload_mode
    )
    spike_delta = round(
        max(2.0, abs(story["noise_shift"]) * 0.9 + max(0, busiest_channel["interference"] - baseline_interference) * 0.25),
        1,
    )
    action = decision["agent"]["action"]
    action_target = decision["agent"]["selected_channel"]

    if story["phase"] == "critical" or overload_mode:
        level = "Critical"
        title = f"CRITICAL: {busiest_channel['id']} congestion detected"
        message = (
            f"Interference spike detected (+{spike_delta}%) on {busiest_channel['id']} "
            f"-> Triggering AI reallocation toward {action_target}."
        )
    elif story["phase"] == "spike":
        level = "Warning"
        title = f"WARNING: {busiest_channel['id']} interference spike"
        message = (
            f"Load drift is accelerating on {busiest_channel['id']} (+{spike_delta}%). "
            f"AI is shifting demand toward {action_target}."
        )
    elif trigger_ai and action["count"] > 0:
        level = "Warning"
        title = f"AI reroute active on {busiest_channel['id']}"
        message = decision["agent"]["action_text"]
    elif story["phase"] == "stabilizing":
        level = "Normal"
        title = "Stabilizing: network pressure easing"
        message = (
            f"Previous surge is settling. {action_target} remains the strongest reserve path while interference drifts down."
        )
    else:
        level = "Normal"
        title = "Normal: live telemetry within range"
        message = (
            f"AI is monitoring all channels. {action_target} currently holds the best balance of headroom and low interference."
        )

    return {
        "level": level,
        "phase": story["phase"],
        "channel": busiest_channel["id"],
        "triggered_ai": trigger_ai,
        "title": title,
        "message": message,
        "spike_delta": spike_delta,
    }


def make_live_signature(frame: dict[str, Any]) -> tuple[Any, ...]:
    simulation = frame["simulation"]
    decision = frame["decision"]
    event = frame["event"]
    return (
        round(simulation["metrics"]["interference"], 1),
        round(decision["metrics"]["optimized_interference"], 1),
        decision["agent"]["selected_channel"],
        event["level"],
        event["phase"],
    )


def build_live_frame(
    payload: SpectrumRequest,
    *,
    session_seed: int | None = None,
    transport: str = "http",
) -> dict[str, Any]:
    seed = make_session_seed(payload.seed or session_seed)
    live_payload, story = build_live_request(payload, seed, payload.tick)
    environment = build_environment(live_payload)
    decision = SIMULATION_POLICY.optimize(
        environment=environment,
        payload=live_payload,
        optimize_fn=optimize_environment,
        mutate_fn=mutate_profile,
        score_fn=score_simulation_decision,
    )
    event = build_live_event(environment, decision, story)

    decision["agent"]["status"] = "active" if event["triggered_ai"] else "monitoring"
    decision["summary"] = (
        f"Rerouting traffic to {decision['agent']['selected_channel']} after "
        f"{event['channel']} crossed the live threshold."
        if event["triggered_ai"]
        else f"AI is scoring routes continuously and keeping {decision['agent']['selected_channel']} primed as the best reserve channel."
    )

    return {
        "transport": transport,
        "stream": {
            "tick": payload.tick,
            "seed": live_payload.seed,
            "phase": story["phase"],
        },
        "event": event,
        "simulation": environment,
        "decision": decision,
    }


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_model=None)
def root() -> Response:
    return built_frontend_response()


@app.post("/api/simulate")
def simulate_environment(payload: SpectrumRequest) -> dict[str, Any]:
    return build_environment(payload)


@app.post("/api/optimize")
def optimize_environment_route(payload: SpectrumRequest) -> dict[str, Any]:
    environment = build_environment(payload)
    return SIMULATION_POLICY.optimize(
        environment=environment,
        payload=payload,
        optimize_fn=optimize_environment,
        mutate_fn=mutate_profile,
        score_fn=score_simulation_decision,
    )


@app.post("/api/live")
def live_environment_route(payload: SpectrumRequest) -> dict[str, Any]:
    return build_live_frame(payload, session_seed=payload.seed, transport="http")


@app.post("/api/network/devices")
async def ingest_device_telemetry(payload: DeviceTelemetryPayload) -> dict[str, Any]:
    frame = LIVE_NETWORK_STORE.ingest(payload)
    await broadcast_network_frame(frame)
    return frame


@app.get("/api/network/snapshot")
def live_network_snapshot() -> dict[str, Any]:
    return LIVE_NETWORK_STORE.snapshot()


@app.post("/api/network/model/save")
def save_live_network_model() -> dict[str, Any]:
    return LIVE_NETWORK_STORE.save_model()


@app.post("/api/network/model/load")
def load_live_network_model() -> dict[str, Any]:
    return LIVE_NETWORK_STORE.load_model()


@app.get("/api/network/probe")
def live_network_probe(size_kb: int = Query(default=64, ge=1, le=256)) -> Response:
    size = size_kb * 1024
    pattern = b"SPECTRUM-PROBE-"
    repeats = (size // len(pattern)) + 1
    payload = (pattern * repeats)[:size]
    return Response(
        content=payload,
        media_type="application/octet-stream",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "X-Probe-Bytes": str(size),
        },
    )


@app.websocket("/ws/live")
async def live_environment_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    base_payload = SpectrumRequest()
    session_seed = make_session_seed()
    tick = 0
    last_signature: tuple[Any, ...] | None = None

    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=2.0)
                message_type = message.get("type", "config")

                if message_type == "config":
                    base_payload = SpectrumRequest(**message.get("payload", {}))
                    session_seed = make_session_seed(base_payload.seed)
                    tick = 0 if message.get("reset", True) else tick
                elif message_type == "snapshot":
                    pass

                frame = build_live_frame(
                    base_payload.model_copy(update={"seed": session_seed, "tick": tick}),
                    session_seed=session_seed,
                    transport="websocket",
                )
                await websocket.send_json(frame)
                last_signature = make_live_signature(frame)
                tick += 1
            except asyncio.TimeoutError:
                frame = build_live_frame(
                    base_payload.model_copy(update={"seed": session_seed, "tick": tick}),
                    session_seed=session_seed,
                    transport="websocket",
                )
                signature = make_live_signature(frame)
                if signature != last_signature:
                    await websocket.send_json(frame)
                    last_signature = signature
                tick += 1
    except WebSocketDisconnect:
        return


@app.websocket("/ws/network")
async def live_network_socket(websocket: WebSocket) -> None:
    await register_network_watcher(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        unregister_network_watcher(websocket)


@app.get("/{full_path:path}", response_model=None)
def frontend_app(full_path: str) -> Response:
    return built_frontend_response(full_path)
