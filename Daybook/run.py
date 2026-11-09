#!/usr/bin/env python3
"""Start Daybook.

    ./start.sh                 # first time: creates .venv, installs, starts
    python3 run.py             # if dependencies are already installed
    python3 run.py --localhost-only --port 8000 --data ~/somewhere

Open the printed address. On first run you'll choose a passphrase; from
another device you'll also need the one-time setup code shown below.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = ["fastapi", "uvicorn", "multipart", "httpx", "dateutil", "cryptography", "mammoth", "markdownify", "bs4",
            "pypdf", "openpyxl", "pptx", "icalendar"]


def check_dependencies() -> None:
    missing = [m for m in REQUIRED if importlib.util.find_spec(m) is None]
    if missing:
        print("Daybook is missing some Python packages:", ", ".join(missing))
        print("\nRun ./start.sh (creates a virtual environment and installs everything), or:")
        print("    python3 -m pip install -r requirements.txt\n")
        sys.exit(1)


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent for UDP connect
            return s.getsockname()[0]
    except OSError:
        return None


def _tailscale_bin() -> str | None:
    return shutil.which("tailscale") or next(
        (p for p in ("/Applications/Tailscale.app/Contents/MacOS/Tailscale",) if Path(p).exists()), None)


def tailscale_info() -> dict:
    exe = _tailscale_bin()
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=3)
        data = json.loads(out.stdout or "{}")
        me = data.get("Self") or {}
        ips = [ip for ip in me.get("TailscaleIPs", []) if "." in ip]
        return {"ip": ips[0] if ips else None, "dns": (me.get("DNSName") or "").rstrip(".") or None}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="Run Daybook")
    ap.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"), help="Interface to listen on (default 0.0.0.0)")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    ap.add_argument("--data", default=os.environ.get("DAYBOOK_DATA"), help="Data folder (default: ~/Library/Application Support/Daybook on macOS)")
    ap.add_argument("--localhost-only", action="store_true", help="Only accept connections from this computer")
    args = ap.parse_args()
    check_dependencies()

    sys.path.insert(0, str(ROOT))
    import uvicorn

    from app import APP_NAME, __version__, db, security
    from app.config import Paths, paths
    from app.main import create_app

    data_dir = Path(args.data).expanduser() if args.data else None
    p = Paths(data_dir) if data_dir else paths
    p.ensure()
    db.configure(p)
    db.migrate()
    host = "127.0.0.1" if args.localhost_only else args.host

    line = "─" * 64
    print(f"\n{line}\n  {APP_NAME} {__version__} — private notes, calendar & tasks\n{line}")
    print(f"  On this Mac:     http://localhost:{args.port}")
    if host != "127.0.0.1":
        ip = lan_ip()
        ts = tailscale_info()
        if ts.get("ip"):
            print(f"  Over Tailscale:  http://{ts['ip']}:{args.port}")
        if ts.get("dns"):
            print(f"  HTTPS (optional): run  tailscale serve --bg {args.port}  then open https://{ts['dns']}")
        if ip:
            print(f"  On your Wi-Fi:   http://{ip}:{args.port}   (unencrypted — prefer Tailscale)")
    print(f"  Your data:       {p.data}")
    try:
        from app.services.library import configured_root

        lib = configured_root()
        print(f"  Library:         {lib}" if lib else "  Library:         none (Settings → Library folder)")
    except Exception:
        pass
    if not security.is_configured():
        print(f"\n  First run: open Daybook and choose a passphrase.")
        print(f"  Setup code for other devices:  {security.setup_code()}")
    print(f"{line}\n  Press Ctrl+C to stop.\n")

    uvicorn.run(create_app(data_dir), host=host, port=args.port, log_level="warning", proxy_headers=False,
                server_header=False, access_log=False)


if __name__ == "__main__":
    main()
