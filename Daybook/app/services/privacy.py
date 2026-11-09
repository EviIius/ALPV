"""Privacy check-up: plain answers to "where does my data go?"."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from .. import db, security
from .ai import check_local_endpoint


def _filevault() -> str:
    if sys.platform != "darwin" or not shutil.which("fdesetup"):
        return "unknown"
    try:
        out = subprocess.run(["fdesetup", "status"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    if "FileVault is On" in out:
        return "on"
    if "FileVault is Off" in out:
        return "off"
    return "unknown"


def _icloud_risk(path: Path) -> str | None:
    p = str(path.resolve())
    home = str(Path.home())
    if "Mobile Documents" in p or "CloudStorage" in p:
        return "This folder is synced to a cloud drive."
    if sys.platform == "darwin" and (p.startswith(home + "/Documents") or p.startswith(home + "/Desktop")):
        if (Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Documents").exists():
            return "Desktop & Documents may be syncing to iCloud Drive."
    return None


def audit(client_kind: str, scheme: str) -> list[dict]:
    s = db.get_settings()
    p = db.get_paths()
    checks: list[dict] = []

    def add(key, status, title, detail):
        checks.append({"key": key, "status": status, "title": title, "detail": detail})

    risk = _icloud_risk(p.data)
    add("storage", "warn" if risk else "ok", "Your data stays on this computer",
        (risk + " " if risk else "") + f"Stored in {p.data}.")
    fv = _filevault()
    add("disk", {"on": "ok", "off": "warn"}.get(fv, "info"), "Disk encryption",
        {"on": "FileVault is on, so the database is encrypted at rest.",
         "off": "FileVault is off. Turn it on in System Settings → Privacy & Security to encrypt everything at rest."}.get(
            fv, "Couldn't check disk encryption on this system."))
    sessions = db.scalar("SELECT count(*) FROM sessions") or 0
    add("signin", "ok", "Sign-in required", f"Every device needs your passphrase. {sessions} device(s) signed in.")
    if scheme == "https":
        add("transport", "ok", "Encrypted connection", "You're connected over HTTPS.")
    elif client_kind == "loopback":
        add("transport", "ok", "Connection stays on this computer", "You're using Daybook on the Mac it runs on.")
    elif client_kind == "tailscale":
        add("transport", "ok", "Encrypted by Tailscale", "Traffic between your devices is end-to-end encrypted by WireGuard.")
    else:
        add("transport", "warn", "Unencrypted on your local network",
            "Other devices on this Wi-Fi could see traffic. Connect over Tailscale, or set up HTTPS (see README).")
    ai = s["ai"]
    if not ai.get("enabled"):
        add("ai", "ok", "Local AI is off", "No AI features run.")
    else:
        loc = check_local_endpoint(ai.get("base_url", ""))
        add("ai", "ok" if loc["ok"] else "error", "AI runs on your own hardware",
            (f"Talking to {urlparse(ai.get('base_url', '')).netloc} on {loc.get('where', 'your network')}. Cloud AI is blocked."
             if loc["ok"] else loc["reason"]))
    n = s["notifications"]
    external = [name for name, on in (("ntfy", n["ntfy"].get("enabled")), ("Discord", n["discord"].get("enabled"))) if on]
    if external:
        add("external", "warn" if not n.get("redact_external") else "info", "Reminders sent outside your devices",
            f"{' and '.join(external)} {'receive' if len(external) > 1 else 'receives'} reminders"
            + (" with titles and details hidden." if n.get("redact_external") else " including titles and details."))
    else:
        add("external", "ok", "No third-party services", "Nothing is sent to outside services. No analytics, no telemetry.")
    feed = s.get("feed") or {}
    if feed.get("enabled"):
        add("feed", "info", "Calendar feed is on",
            "Your phone's Calendar can subscribe to a secret link that only works on this Mac, your tailnet or your home network"
            + (" and shows only busy times." if feed.get("detail") == "busy" else
               " and shows event titles only." if feed.get("detail") == "titles" else ".")
            + " Reset the link in Settings → Calendars to cut off old subscriptions.")
    add("browser", "ok", "Browser locked down",
        "The app can only connect to this server. Fonts, icons and scripts are served locally, and remote images in notes are blocked.")
    locked = db.scalar("SELECT count(*) FROM notes WHERE locked = 1 AND trashed_at IS NULL") or 0
    add("vault", "ok" if locked else "info", "Locked notes",
        f"{locked} note(s) encrypted with your vault passphrase." if locked else "Lock sensitive notes to encrypt them with a separate passphrase.")
    last = s["backups"].get("last") or 0
    add("backups", "ok" if last else "info", "Local backups",
        "Daily snapshots are kept in " + str(p.backups) + "." if s["backups"].get("auto") else "Automatic snapshots are off.")
    return checks


def summary() -> dict:
    p = db.get_paths()
    size = 0
    for root, _dirs, files in os.walk(p.data):
        for f in files:
            try:
                size += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return {"data_dir": str(p.data), "bytes": size, "vault_set": security.vault_is_set()}
