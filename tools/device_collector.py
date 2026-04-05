from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from statistics import mean
from uuid import uuid4


def fetch_bytes(url: str) -> tuple[bytes, float]:
    started_at = time.perf_counter()
    with urllib.request.urlopen(url) as response:
        payload = response.read()
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    return payload, elapsed_ms


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read().decode("utf-8"))


def measure_probe(base_url: str, size_kb: int) -> tuple[float, float]:
    probe_url = f"{base_url}/api/network/probe?{urllib.parse.urlencode({'size_kb': size_kb, 'ts': int(time.time() * 1000)})}"
    payload, elapsed_ms = fetch_bytes(probe_url)
    throughput_mbps = (len(payload) * 8) / max(elapsed_ms, 1) / 1000
    return elapsed_ms, throughput_mbps


def measure_latency_window(base_url: str, attempts: int = 4) -> tuple[float, float]:
    latencies: list[float] = []

    for attempt in range(attempts):
        try:
            latency_ms, _ = measure_probe(base_url, 4)
            latencies.append(latency_ms)
        except urllib.error.URLError:
            pass

        if attempt < attempts - 1:
            time.sleep(0.08)

    if not latencies:
        raise urllib.error.URLError("All latency probes failed")

    deltas = [abs(latencies[index] - latencies[index - 1]) for index in range(1, len(latencies))]
    jitter_ms = mean(deltas) if deltas else 0.0
    return mean(latencies), jitter_ms


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish real telemetry to the AI spectrum sharing backend.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend base URL")
    parser.add_argument("--device-id", default=f"python-{uuid4().hex[:8]}", help="Stable device ID to publish")
    parser.add_argument("--interval", type=float, default=2.0, help="Publish interval in seconds")
    parser.add_argument("--probe-kb", type=int, default=64, help="Probe size for throughput estimation")
    parser.add_argument("--band", default=None, help="Optional real network band label such as wifi-5ghz")
    args = parser.parse_args()

    print(f"Starting device collector for {args.device_id} -> {args.base_url}")

    while True:
        try:
            avg_latency_ms, jitter_ms = measure_latency_window(args.base_url, 4)
            _, throughput_mbps = measure_probe(args.base_url, args.probe_kb)
            payload = {
                "device_id": args.device_id,
                "latency_ms": round(avg_latency_ms, 2),
                "throughput_mbps": round(throughput_mbps, 2),
                "jitter_ms": round(jitter_ms, 2),
            }
            if args.band:
                payload["band"] = args.band

            snapshot = post_json(f"{args.base_url}/api/network/devices", payload)
            print(
                f"[ok] latency={avg_latency_ms:.1f} ms jitter={jitter_ms:.1f} ms "
                f"throughput={throughput_mbps:.1f} Mbps devices={snapshot['metrics']['device_count']} "
                f"anchor={snapshot['agent']['anchor_device']} "
                f"event={snapshot['event']['level']}"
            )
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as error:
            print(f"[error] {error}")

        time.sleep(max(args.interval, 0.5))


if __name__ == "__main__":
    main()
