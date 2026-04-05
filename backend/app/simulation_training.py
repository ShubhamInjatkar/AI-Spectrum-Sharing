from __future__ import annotations

import json
import random
from collections import deque
from pathlib import Path
from statistics import mean
from threading import Lock
from typing import Any, Callable


class SimulationPolicyTrainer:
    def __init__(
        self,
        *,
        cache_path: Path,
        version: str,
        base_profile: dict[str, float],
    ) -> None:
        self.cache_path = cache_path
        self.version = version
        self.profile = base_profile.copy()
        self.training_steps = 0
        self.update_steps = 0
        self.last_reward = 0.0
        self.last_gain = 0.0
        self.reward_window: deque[float] = deque(maxlen=72)
        self._lock = Lock()
        self.checkpoint_loaded = self._load()

    def _load(self) -> bool:
        if not self.cache_path.exists():
            return False

        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False

        if payload.get("version") != self.version or "weights" not in payload:
            return False

        self.profile = payload["weights"]
        self.training_steps = int(payload.get("training_steps", 0))
        self.update_steps = int(payload.get("update_steps", 0))
        self.last_reward = float(payload.get("last_reward", 0.0))
        self.last_gain = float(payload.get("last_gain", 0.0))
        for reward in payload.get("reward_window", []):
            self.reward_window.append(float(reward))
        return True

    def _save(self) -> None:
        payload = {
            "version": self.version,
            "weights": self.profile,
            "training_steps": self.training_steps,
            "update_steps": self.update_steps,
            "last_reward": round(self.last_reward, 4),
            "last_gain": round(self.last_gain, 4),
            "reward_window": [round(value, 4) for value in self.reward_window],
        }
        try:
            self.cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            return

    def _status_snapshot(self, candidate_count: int) -> dict[str, Any]:
        average_reward = mean(self.reward_window) if self.reward_window else 0.0
        return {
            "enabled": True,
            "kind": "adaptive",
            "label": "Simulation Trainer",
            "badge": "online tuning",
            "summary": "Simulation mode keeps retuning channel-scoring weights after each scenario.",
            "training_steps": self.training_steps,
            "update_steps": self.update_steps,
            "candidate_count": candidate_count,
            "average_reward": round(average_reward, 3),
            "last_reward": round(self.last_reward, 3),
            "reward_gain": round(self.last_gain, 3),
            "checkpoint_loaded": self.checkpoint_loaded,
            "status_items": [
                {"label": "Updates", "value": str(self.update_steps), "accent": "text-cyan-100"},
                {"label": "Episodes", "value": str(self.training_steps), "accent": "text-violet-100"},
                {
                    "label": "Avg reward",
                    "value": f"{round(average_reward, 2):.2f}",
                    "accent": "text-pink-100",
                },
                {
                    "label": "Last gain",
                    "value": f"{self.last_gain:+.2f}",
                    "accent": "text-emerald-100",
                },
                {
                    "label": "Candidates",
                    "value": str(candidate_count),
                    "accent": "text-slate-200",
                },
            ],
        }

    def optimize(
        self,
        *,
        environment: dict[str, Any],
        payload: Any,
        optimize_fn: Callable[..., dict[str, Any]],
        mutate_fn: Callable[[dict[str, float], random.Random, float], dict[str, float]],
        score_fn: Callable[[dict[str, Any]], float],
    ) -> dict[str, Any]:
        with self._lock:
            current_result = optimize_fn(environment, payload, profile=self.profile)
            current_reward = score_fn(current_result)

            rng = random.Random(environment["seed"] + payload.tick * 37 + self.training_steps * 101)
            candidate_count = 5 if self.training_steps < 80 else 4
            intensity = max(0.03, 0.16 - min(self.training_steps, 120) * 0.001)

            best_result = current_result
            best_reward = current_reward
            best_profile = self.profile

            for _ in range(candidate_count):
                candidate_profile = mutate_fn(self.profile, rng, intensity)
                candidate_result = optimize_fn(environment, payload, profile=candidate_profile)
                candidate_reward = score_fn(candidate_result)
                if candidate_reward > best_reward + 0.03:
                    best_result = candidate_result
                    best_reward = candidate_reward
                    best_profile = candidate_profile

            self.training_steps += 1
            self.last_reward = best_reward
            self.last_gain = best_reward - current_reward
            self.reward_window.append(best_reward)

            if best_profile != self.profile:
                self.profile = best_profile
                self.update_steps += 1
                if self.update_steps % 8 == 0:
                    self._save()
            elif self.training_steps % 16 == 0:
                self._save()

            best_result["training"] = self._status_snapshot(candidate_count)
            return best_result
