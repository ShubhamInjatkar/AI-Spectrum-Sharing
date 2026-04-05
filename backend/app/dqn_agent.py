from __future__ import annotations

import random
from collections import deque
from pathlib import Path
from typing import Any

try:
    import torch
    from torch import nn
except Exception:
    torch = None
    nn = None


ACTIONS = ("monitor", "promote_anchor", "deprioritize")
STATE_SIZE = 9
MODEL_VERSION = "live-dqn-v3"


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(value, maximum))


def average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def band_feature(band: str | None) -> float:
    if not band:
        return 0.0

    band_text = band.lower()
    if "6" in band_text:
        return 1.0
    if "5" in band_text:
        return 0.8
    if "2.4" in band_text or "2g" in band_text:
        return 0.4
    if "lte" in band_text or "4g" in band_text:
        return 0.65
    if "5g" in band_text:
        return 0.9
    return 0.6


class ReplayBuffer:
    def __init__(self, capacity: int = 4096) -> None:
        self._buffer: deque[tuple[list[float], int, float, list[float], float]] = deque(maxlen=capacity)

    def push(self, state: list[float], action: int, reward: float, next_state: list[float], done: bool) -> None:
        self._buffer.append((state, action, reward, next_state, float(done)))

    def sample(self, batch_size: int) -> tuple[list[list[float]], list[int], list[float], list[list[float]], list[float]]:
        batch = random.sample(self._buffer, batch_size)
        states, actions, rewards, next_states, dones = zip(*batch)
        return list(states), list(actions), list(rewards), list(next_states), list(dones)

    def __len__(self) -> int:
        return len(self._buffer)


if nn is not None:

    class DQNModel(nn.Module):
        def __init__(self, input_dim: int, output_dim: int) -> None:
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(input_dim, 128),
                nn.LayerNorm(128),
                nn.ReLU(),
                nn.Linear(128, 128),
                nn.ReLU(),
                nn.Linear(128, output_dim),
            )

        def forward(self, inputs: torch.Tensor) -> torch.Tensor:
            return self.net(inputs)


class LiveTelemetryDQN:
    def __init__(self, checkpoint_path: Path) -> None:
        self.checkpoint_path = checkpoint_path
        self.available = torch is not None and nn is not None
        self.buffer = ReplayBuffer()
        self.gamma = 0.94
        self.batch_size = 8
        self.target_sync_interval = 20
        self.save_interval = 20
        self.epsilon_start = 0.28
        self.epsilon_end = 0.04
        self.epsilon_decay = 2200
        self.training_steps = 0
        self.update_steps = 0
        self.last_loss: float | None = None
        self.reward_window: deque[float] = deque(maxlen=128)
        self._last_observations: dict[str, dict[str, Any]] = {}
        self._loaded_from_checkpoint = False

        if not self.available:
            self.policy_net = None
            self.target_net = None
            self.optimizer = None
            return

        self.device = torch.device("cpu")
        self.policy_net = DQNModel(STATE_SIZE, len(ACTIONS)).to(self.device)
        self.target_net = DQNModel(STATE_SIZE, len(ACTIONS)).to(self.device)
        self.target_net.load_state_dict(self.policy_net.state_dict())
        self.optimizer = torch.optim.Adam(self.policy_net.parameters(), lr=1e-3)
        self._loaded_from_checkpoint = self.load()

    def epsilon(self) -> float:
        if not self.available:
            return 0.0
        decay_ratio = min(1.0, self.training_steps / self.epsilon_decay)
        return self.epsilon_start - (self.epsilon_start - self.epsilon_end) * decay_ratio

    def _state_vector(self, device: dict[str, Any], metrics: dict[str, Any]) -> list[float]:
        return [
            clamp(device["latency_ms"] / 250.0, 0.0, 2.0),
            clamp((device["throughput_mbps"] or 0.0) / 150.0, 0.0, 2.0),
            clamp(device["jitter_ms"] / 80.0, 0.0, 2.0),
            clamp(device["computed_load"] / 100.0, 0.0, 1.0),
            clamp(device["computed_interference"] / 100.0, 0.0, 1.0),
            clamp(device["computed_noise"] / 100.0, 0.0, 1.0),
            clamp(metrics["occupancy"] / 100.0, 0.0, 1.0),
            clamp(metrics["interference"] / 100.0, 0.0, 1.0),
            band_feature(device.get("band")),
        ]

    def _reward(self, previous: dict[str, Any], current: dict[str, Any], metrics: dict[str, Any], action: int) -> float:
        latency_gain = previous["latency_ms"] - current["latency_ms"]
        throughput_gain = (current.get("throughput_mbps") or 0.0) - (previous.get("throughput_mbps") or 0.0)
        jitter_gain = previous["jitter_ms"] - current["jitter_ms"]
        score_gain = current["performance_score"] - previous["performance_score"]

        reward = (
            score_gain * 0.08
            + latency_gain * 0.03
            + throughput_gain * 0.01
            + jitter_gain * 0.02
            - metrics["interference"] * 0.002
        )

        if action == 1:
            reward += 0.35 if current["performance_score"] >= previous["performance_score"] else -0.2
        elif action == 2:
            reward += (
                0.35
                if current["computed_interference"] <= previous["computed_interference"]
                else -0.2
            )
        else:
            reward += 0.2 if abs(latency_gain) < 8 and current["jitter_ms"] <= previous["jitter_ms"] else -0.1

        return round(clamp(reward, -12.0, 12.0), 4)

    def _predict_q_values(self, state: list[float]) -> list[float]:
        if not self.available or self.policy_net is None:
            return [0.0 for _ in ACTIONS]

        with torch.no_grad():
            tensor_state = torch.tensor(state, dtype=torch.float32, device=self.device).unsqueeze(0)
            q_values = self.policy_net(tensor_state).squeeze(0).tolist()
        return [float(value) for value in q_values]

    def _pick_action(self, state: list[float], *, explore: bool) -> tuple[int, list[float], bool]:
        q_values = self._predict_q_values(state)
        if not self.available:
            return 0, q_values, False

        epsilon = self.epsilon()
        if explore and random.random() < epsilon:
            return random.randrange(len(ACTIONS)), q_values, True

        best_action = max(range(len(q_values)), key=lambda index: q_values[index])
        return best_action, q_values, False

    def _confidence_from_q_values(self, q_values: list[float]) -> float:
        ordered = sorted(q_values, reverse=True)
        gap = ordered[0] - ordered[1] if len(ordered) > 1 else ordered[0]
        return round(clamp(58.0 + gap * 18.0, 55.0, 97.0), 1)

    def _train_step(self) -> None:
        if not self.available or self.policy_net is None or self.target_net is None or self.optimizer is None:
            return
        if len(self.buffer) < 2:
            return

        actual_batch_size = min(len(self.buffer), self.batch_size)
        states, actions, rewards, next_states, dones = self.buffer.sample(actual_batch_size)
        state_tensor = torch.tensor(states, dtype=torch.float32, device=self.device)
        action_tensor = torch.tensor(actions, dtype=torch.int64, device=self.device).unsqueeze(1)
        reward_tensor = torch.tensor(rewards, dtype=torch.float32, device=self.device)
        next_state_tensor = torch.tensor(next_states, dtype=torch.float32, device=self.device)
        done_tensor = torch.tensor(dones, dtype=torch.float32, device=self.device)

        current_q = self.policy_net(state_tensor).gather(1, action_tensor).squeeze(1)
        with torch.no_grad():
            next_q = self.target_net(next_state_tensor).max(dim=1).values
            target_q = reward_tensor + (1.0 - done_tensor) * self.gamma * next_q

        loss = nn.functional.smooth_l1_loss(current_q, target_q)
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.policy_net.parameters(), max_norm=1.0)
        self.optimizer.step()

        self.last_loss = float(loss.item())
        self.update_steps += 1

        if self.update_steps % self.target_sync_interval == 0:
            self.target_net.load_state_dict(self.policy_net.state_dict())
        if self.update_steps % self.save_interval == 0:
            self.save()

    def observe(self, devices: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
        policy_updates: dict[str, dict[str, Any]] = {}
        if not devices:
            self._last_observations.clear()
            return self.status_snapshot(policy_updates=policy_updates)

        active_device_ids: set[str] = set()
        for device in devices:
            device_id = device["device_id"]
            active_device_ids.add(device_id)
            state = self._state_vector(device, metrics)

            previous = self._last_observations.get(device_id)
            if previous is not None:
                reward = self._reward(previous["device"], device, metrics, previous["action"])
                self.buffer.push(previous["state"], previous["action"], reward, state, False)
                self.reward_window.append(reward)

            exploratory_action, _, explored = self._pick_action(state, explore=True)
            greedy_action, greedy_q_values, _ = self._pick_action(state, explore=False)
            policy_updates[device_id] = {
                "recommended_action": ACTIONS[greedy_action],
                "exploratory_action": ACTIONS[exploratory_action],
                "explored": explored,
                "confidence": self._confidence_from_q_values(greedy_q_values),
                "q_values": {
                    ACTIONS[index]: round(float(value), 4)
                    for index, value in enumerate(greedy_q_values)
                },
            }
            self._last_observations[device_id] = {
                "state": state,
                "action": exploratory_action,
                "device": {
                    "latency_ms": device["latency_ms"],
                    "throughput_mbps": device["throughput_mbps"] or 0.0,
                    "jitter_ms": device["jitter_ms"],
                    "computed_interference": device["computed_interference"],
                    "performance_score": device["performance_score"],
                },
            }

        for stale_id in list(self._last_observations):
            if stale_id not in active_device_ids:
                self._last_observations.pop(stale_id, None)

        self.training_steps += 1
        self._train_step()
        return self.status_snapshot(policy_updates=policy_updates)

    def infer(self, devices: list[dict[str, Any]], metrics: dict[str, Any]) -> dict[str, Any]:
        policy_updates: dict[str, dict[str, Any]] = {}
        for device in devices:
            state = self._state_vector(device, metrics)
            greedy_action, greedy_q_values, _ = self._pick_action(state, explore=False)
            policy_updates[device["device_id"]] = {
                "recommended_action": ACTIONS[greedy_action],
                "exploratory_action": ACTIONS[greedy_action],
                "explored": False,
                "confidence": self._confidence_from_q_values(greedy_q_values),
                "q_values": {
                    ACTIONS[index]: round(float(value), 4)
                    for index, value in enumerate(greedy_q_values)
                },
            }
        return self.status_snapshot(policy_updates=policy_updates)

    def status_snapshot(
        self,
        *,
        policy_updates: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        average_reward = average(list(self.reward_window))
        return {
            "enabled": self.available,
            "kind": "dqn",
            "label": "Live DQN Trainer",
            "badge": "real telemetry only",
            "summary": (
                "The live learner updates from real telemetry only and never injects simulated values."
                if self.available
                else "PyTorch is not installed yet, so the live DQN definition is present but inactive."
            ),
            "model": "DQN",
            "version": MODEL_VERSION,
            "checkpoint_loaded": self._loaded_from_checkpoint,
            "training_steps": self.training_steps,
            "update_steps": self.update_steps,
            "buffer_size": len(self.buffer),
            "epsilon": round(self.epsilon(), 4) if self.available else 0.0,
            "last_loss": round(self.last_loss, 6) if self.last_loss is not None else None,
            "average_reward": round(average_reward, 4),
            "policy_updates": policy_updates or {},
            "status_items": [
                {"label": "Updates", "value": str(self.update_steps), "accent": "text-cyan-100"},
                {"label": "Replay", "value": str(len(self.buffer)), "accent": "text-violet-100"},
                {
                    "label": "Epsilon",
                    "value": f"{round(self.epsilon(), 3):.3f}" if self.available else "0.000",
                    "accent": "text-pink-100",
                },
                {
                    "label": "Avg reward",
                    "value": f"{round(average_reward, 2):.2f}",
                    "accent": "text-emerald-100",
                },
                {
                    "label": "Loss",
                    "value": "n/a" if self.last_loss is None else f"{round(self.last_loss, 4):.4f}",
                    "accent": "text-slate-200",
                },
            ],
        }

    def save(self) -> dict[str, Any]:
        if not self.available or self.policy_net is None or self.target_net is None or self.optimizer is None:
            return {"saved": False, "enabled": False}

        payload = {
            "version": MODEL_VERSION,
            "policy_state": self.policy_net.state_dict(),
            "target_state": self.target_net.state_dict(),
            "optimizer_state": self.optimizer.state_dict(),
            "training_steps": self.training_steps,
            "update_steps": self.update_steps,
            "last_loss": self.last_loss,
        }
        torch.save(payload, self.checkpoint_path)
        return {"saved": True, "enabled": True, "path": str(self.checkpoint_path)}

    def load(self) -> bool:
        if not self.available or self.policy_net is None or self.target_net is None or self.optimizer is None:
            return False
        if not self.checkpoint_path.exists():
            return False

        payload = torch.load(self.checkpoint_path, map_location=self.device)
        if payload.get("version") != MODEL_VERSION:
            return False

        self.policy_net.load_state_dict(payload["policy_state"])
        self.target_net.load_state_dict(payload["target_state"])
        self.optimizer.load_state_dict(payload["optimizer_state"])
        self.training_steps = int(payload.get("training_steps", 0))
        self.update_steps = int(payload.get("update_steps", 0))
        self.last_loss = payload.get("last_loss")
        return True
