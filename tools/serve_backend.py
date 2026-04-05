from __future__ import annotations

import argparse
import ipaddress
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIRTUAL_INTERFACE_HINTS = ("vmware", "virtual", "hyper-v", "loopback", "vbox", "teredo", "wsl")


def parse_windows_interfaces() -> list[dict[str, str]]:
    result = subprocess.run(
        ["ipconfig"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    interfaces: list[dict[str, str]] = []
    current_name: str | None = None
    current_disconnected = False
    current_ipv4: str | None = None

    for raw_line in result.stdout.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if line and not line.startswith(" ") and stripped.endswith(":"):
            if current_name and current_ipv4 and not current_disconnected:
                interfaces.append({"name": current_name, "ip": current_ipv4})
            current_name = stripped[:-1]
            current_disconnected = False
            current_ipv4 = None
            continue

        if not current_name:
            continue

        if "Media State" in stripped and "disconnected" in stripped.lower():
            current_disconnected = True
            continue

        if "IPv4 Address" in stripped:
            maybe_ip = stripped.split(":")[-1].strip()
            try:
                parsed = ipaddress.ip_address(maybe_ip)
            except ValueError:
                continue
            if parsed.version == 4 and not parsed.is_loopback:
                current_ipv4 = maybe_ip

    if current_name and current_ipv4 and not current_disconnected:
        interfaces.append({"name": current_name, "ip": current_ipv4})

    return interfaces


def parse_generic_interfaces() -> list[dict[str, str]]:
    interfaces: list[dict[str, str]] = []
    try:
        host_info = subprocess.run(
            ["hostname", "-I"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        for index, candidate in enumerate(host_info.stdout.split(), start=1):
            try:
                parsed = ipaddress.ip_address(candidate)
            except ValueError:
                continue
            if parsed.version == 4 and not parsed.is_loopback:
                interfaces.append({"name": f"Interface {index}", "ip": candidate})
    except Exception:
        pass
    return interfaces


def get_interface_candidates() -> list[dict[str, str]]:
    if sys.platform.startswith("win"):
        candidates = parse_windows_interfaces()
    else:
        candidates = parse_generic_interfaces()

    seen: set[str] = set()
    filtered: list[dict[str, str]] = []
    for item in candidates:
        ip = item["ip"]
        if ip in seen:
            continue
        seen.add(ip)
        filtered.append(item)

    preferred = [
        item
        for item in filtered
        if not any(hint in item["name"].lower() for hint in VIRTUAL_INTERFACE_HINTS)
    ]
    return preferred or filtered


def filter_interfaces(
    interfaces: list[dict[str, str]],
    selected_interfaces: list[str] | None,
    selected_hosts: list[str] | None,
) -> list[dict[str, str]]:
    filtered = interfaces

    if selected_interfaces:
        wanted = [entry.lower() for entry in selected_interfaces]
        filtered = [
            item
            for item in filtered
            if any(wanted_name in item["name"].lower() for wanted_name in wanted)
        ]

    if selected_hosts:
        filtered = [item for item in filtered if item["ip"] in selected_hosts]

    return filtered


def print_links(hosts: list[dict[str, str]], port: int) -> None:
    print()
    print("Access links")
    print("------------")
    print(f"Local dashboard: http://127.0.0.1:{port}/")
    print(f"Local collector: http://127.0.0.1:{port}/collector/")

    for item in hosts:
        print()
        print(f"{item['name']}")
        print(f"Dashboard: http://{item['ip']}:{port}/")
        print(f"Collector: http://{item['ip']}:{port}/collector/")

    print()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve the backend and print clickable dashboard/collector links for one or more network interfaces.",
    )
    parser.add_argument("--port", type=int, default=8000, help="Port for the FastAPI server")
    parser.add_argument(
        "--interfaces",
        nargs="+",
        help='Optional interface name filters such as "Wi-Fi" or "Ethernet"',
    )
    parser.add_argument(
        "--hosts",
        nargs="+",
        help="Optional explicit IPv4 addresses to print",
    )
    parser.add_argument(
        "--print-only",
        action="store_true",
        help="Print the generated links without starting the backend",
    )
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Start uvicorn without --reload",
    )
    args = parser.parse_args()

    if args.hosts:
        for host in args.hosts:
            ipaddress.ip_address(host)

    candidates = get_interface_candidates()
    selected = filter_interfaces(candidates, args.interfaces, args.hosts)

    if not selected and args.hosts:
        selected = [{"name": "Selected host", "ip": host} for host in args.hosts]

    print_links(selected, args.port)

    if args.print_only:
        raise SystemExit(0)

    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(args.port),
    ]
    if not args.no_reload:
        command.append("--reload")

    print("Starting backend server...")
    print()
    raise SystemExit(
        subprocess.call(
            command,
            cwd=PROJECT_ROOT,
        )
    )


if __name__ == "__main__":
    main()
