from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from serve_backend import PROJECT_ROOT, filter_interfaces, get_interface_candidates, print_links


PUBLIC_URL_PATTERNS = (
    re.compile(r"https://[a-zA-Z0-9.-]+\.trycloudflare\.com"),
    re.compile(r"https://[a-zA-Z0-9.-]+\.ngrok(?:-free)?\.app"),
    re.compile(r"https://[a-zA-Z0-9.-]+\.ngrok\.io"),
)


def ensure_frontend_built(force_build: bool) -> None:
    dist_index = PROJECT_ROOT / "frontend" / "dist" / "index.html"
    if dist_index.exists() and not force_build:
        return

    print("Building frontend for backend serving...")
    subprocess.run(
        ["npm", "run", "build"],
        cwd=PROJECT_ROOT / "frontend",
        check=True,
    )


def wait_for_backend(port: int, timeout_seconds: int = 45) -> None:
    deadline = time.time() + timeout_seconds
    health_url = f"http://127.0.0.1:{port}/api/health"

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=3) as response:
                if response.status == 200:
                    return
        except urllib.error.URLError:
            pass
        time.sleep(0.8)

    raise TimeoutError(f"Backend did not become ready at {health_url}")


def find_public_url_in_line(line: str) -> str | None:
    for pattern in PUBLIC_URL_PATTERNS:
        match = pattern.search(line)
        if match:
            return match.group(0)
    return None


def stream_output_for_url(
    process: subprocess.Popen[str],
    *,
    prefix: str,
    found: dict[str, str | None],
) -> threading.Thread:
    def runner() -> None:
        if process.stdout is None:
            return

        for line in process.stdout:
            text = line.rstrip()
            if text:
                print(f"[{prefix}] {text}")
            if not found["url"]:
                maybe_url = find_public_url_in_line(text)
                if maybe_url:
                    found["url"] = maybe_url

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread


def wait_for_ngrok_url(timeout_seconds: int = 45) -> str:
    deadline = time.time() + timeout_seconds
    api_url = "http://127.0.0.1:4040/api/tunnels"

    while time.time() < deadline:
        try:
            with urllib.request.urlopen(api_url, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            tunnels = payload.get("tunnels", [])
            https_tunnel = next(
                (item.get("public_url") for item in tunnels if str(item.get("public_url", "")).startswith("https://")),
                None,
            )
            if https_tunnel:
                return str(https_tunnel)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError):
            pass
        time.sleep(0.8)

    raise TimeoutError("ngrok did not expose a public HTTPS URL in time")


def start_tunnel(provider: str, port: int) -> tuple[subprocess.Popen[str], str]:
    backend_url = f"http://127.0.0.1:{port}"

    if provider == "cloudflared":
        executable = shutil.which("cloudflared")
        if not executable:
            raise FileNotFoundError("cloudflared is not installed or not on PATH")

        process = subprocess.Popen(
            [executable, "tunnel", "--url", backend_url, "--no-autoupdate"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        found = {"url": None}
        stream_output_for_url(process, prefix="cloudflared", found=found)
        deadline = time.time() + 45
        while time.time() < deadline:
            if process.poll() is not None:
                raise RuntimeError("cloudflared exited before a public URL was created")
            if found["url"]:
                return process, str(found["url"])
            time.sleep(0.4)
        raise TimeoutError("cloudflared did not produce a public URL in time")

    if provider == "ngrok":
        executable = shutil.which("ngrok")
        if not executable:
            raise FileNotFoundError("ngrok is not installed or not on PATH")

        process = subprocess.Popen(
            [executable, "http", str(port), "--log", "stdout"],
            cwd=PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        found = {"url": None}
        stream_output_for_url(process, prefix="ngrok", found=found)
        public_url = wait_for_ngrok_url()
        return process, public_url

    raise ValueError(f"Unsupported provider: {provider}")


def choose_provider(requested: str) -> str:
    if requested != "auto":
        return requested

    if shutil.which("cloudflared"):
        return "cloudflared"
    if shutil.which("ngrok"):
        return "ngrok"
    raise FileNotFoundError("No supported tunnel provider found. Install cloudflared or ngrok.")


def print_public_links(public_url: str) -> None:
    root = public_url.rstrip("/")
    print()
    print("Public links")
    print("------------")
    print(f"Dashboard: {root}/")
    print(f"Collector: {root}/collector/")
    print()


def terminate_process(process: subprocess.Popen[str] | None, *, name: str) -> None:
    if process is None or process.poll() is not None:
        return

    print(f"Stopping {name}...")
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start the backend and expose a public dashboard + collector URL using cloudflared or ngrok.",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for the backend server")
    parser.add_argument(
        "--provider",
        choices=("auto", "cloudflared", "ngrok"),
        default="auto",
        help="Tunnel provider to use",
    )
    parser.add_argument(
        "--interfaces",
        nargs="+",
        help='Optional local interface name filters such as "Wi-Fi" or "Ethernet"',
    )
    parser.add_argument(
        "--hosts",
        nargs="+",
        help="Optional explicit local IPv4 addresses to print",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        help="Force a frontend rebuild before starting",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Start uvicorn with --reload",
    )
    args = parser.parse_args()

    if args.hosts:
        for host in args.hosts:
            ipaddress.ip_address(host)

    candidates = get_interface_candidates()
    selected = filter_interfaces(candidates, args.interfaces, args.hosts)
    if not selected and args.hosts:
        selected = [{"name": "Selected host", "ip": host} for host in args.hosts]

    ensure_frontend_built(force_build=args.build)
    print_links(selected, args.port)

    provider = choose_provider(args.provider)
    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(args.port),
    ]
    if args.reload:
        backend_command.append("--reload")

    backend_process: subprocess.Popen[str] | None = None
    tunnel_process: subprocess.Popen[str] | None = None

    try:
        print("Starting backend server...")
        print()
        backend_process = subprocess.Popen(backend_command, cwd=PROJECT_ROOT)
        wait_for_backend(args.port)

        print(f"Starting public tunnel with {provider}...")
        tunnel_process, public_url = start_tunnel(provider, args.port)
        print_public_links(public_url)
        print("Press CTRL+C to stop both the backend and the public tunnel.")
        print()

        raise SystemExit(backend_process.wait())
    except KeyboardInterrupt:
        print()
        print("Stopping public mode...")
    finally:
        terminate_process(tunnel_process, name="public tunnel")
        terminate_process(backend_process, name="backend server")


if __name__ == "__main__":
    main()
