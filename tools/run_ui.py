#!/usr/bin/env python3
"""Start one clean NFL GM UI runner process.

This is a small convenience wrapper for playtesting. It removes stale
ui_runner.py processes, starts a fresh server, waits for /api/state, and prints
the URLs that matter.
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
RUNNER = ROOT / "tools" / "ui_runner.py"


def powershell() -> str:
    return "powershell.exe"


def stop_existing_runners() -> None:
    command = r"""
$procs = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -match '^python' -and
    ($_.CommandLine -match 'tools[\\/]ui_runner\.py' -or $_.CommandLine -match 'ui_runner\.py')
  }
foreach ($p in $procs) {
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
}
"""
    subprocess.run(
        [powershell(), "-NoProfile", "-Command", command],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def start_runner(host: str, port: int) -> subprocess.Popen:
    LOG_DIR.mkdir(exist_ok=True)
    stdout_path = LOG_DIR / "ui_runner.out.log"
    stderr_path = LOG_DIR / "ui_runner.err.log"
    stdout = stdout_path.open("w", encoding="utf-8")
    stderr = stderr_path.open("w", encoding="utf-8")
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    return subprocess.Popen(
        [sys.executable, str(RUNNER), "--host", host, "--port", str(port)],
        cwd=ROOT,
        stdout=stdout,
        stderr=stderr,
        creationflags=creationflags,
    )


def wait_for_health(host: str, port: int, timeout_seconds: float = 45.0) -> tuple[bool, str]:
    health_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{health_host}:{port}/api/state"
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=8) as response:
                if response.status == 200:
                    return True, ""
                last_error = f"HTTP {response.status}"
        except (OSError, urllib.error.URLError) as exc:
            last_error = str(exc)
        time.sleep(1)
    return False, last_error


def lan_addresses() -> list[str]:
    addresses: list[str] = []
    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = item[4][0]
            if address.startswith("127.") or address in addresses:
                continue
            addresses.append(address)
    except OSError:
        pass
    return addresses


def main() -> int:
    parser = argparse.ArgumentParser(description="Restart the NFL GM UI runner cleanly.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-kill", action="store_true", help="Do not stop existing ui_runner.py processes first.")
    args = parser.parse_args()

    if not args.no_kill:
        stop_existing_runners()
        time.sleep(1)

    process = start_runner(args.host, args.port)
    ok, error = wait_for_health(args.host, args.port)
    local_base = f"http://127.0.0.1:{args.port}" if args.host == "0.0.0.0" else f"http://{args.host}:{args.port}"
    print(f"UI runner PID: {process.pid}")
    print(f"App Shell:   {local_base}/ui/app_shell/index.html")
    print(f"Game Center: {local_base}/ui/game_center/index.html")
    if args.host == "0.0.0.0":
        for address in lan_addresses():
            lan_base = f"http://{address}:{args.port}"
            print(f"LAN URL:     {lan_base}/ui/app_shell/index.html")
    print(f"Logs:        {LOG_DIR / 'ui_runner.out.log'}")
    if ok:
        print("Status:      ready")
        return 0
    print(f"Status:      started, but health check did not complete: {error}")
    print(f"Error Log:   {LOG_DIR / 'ui_runner.err.log'}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
