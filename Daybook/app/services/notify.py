"""Notification channels.

Private by default: in-app (live to every open Daybook window) and macOS
Notification Center on the host. ntfy and Discord are optional, off by
default, and receive redacted text unless you turn redaction off.
"""
from __future__ import annotations

import re
import subprocess
import sys

import httpx

from .. import db
from ..bus import bus
from ..util import now_ms

DISCORD_RX = re.compile(r"^https://(discord|discordapp)\.com/api/webhooks/\d+/[\w-]+$")


def _store_inapp(title: str, body: str, kind: str, ref: str | None) -> None:
    with db.tx() as c:
        c.execute("INSERT INTO inbox_items(title, body, kind, ref, created_at) VALUES (?,?,?,?,?)",
                  (title[:200], body[:1000], kind, ref, now_ms()))
        c.execute("DELETE FROM inbox_items WHERE id NOT IN (SELECT id FROM inbox_items ORDER BY id DESC LIMIT 200)")
    bus.publish({"type": "notify", "title": title, "body": body, "kind": kind, "ref": ref})


def _macos(title: str, body: str) -> bool:
    if sys.platform != "darwin":
        return False
    script = ["osascript", "-e", "on run argv", "-e",
              "display notification (item 2 of argv) with title (item 1 of argv) sound name \"Glass\"", "-e", "end run",
              title[:120], body[:240]]
    try:
        subprocess.run(script, capture_output=True, timeout=10)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ntfy(cfg: dict, title: str, body: str, priority: str = "default") -> None:
    url = (cfg.get("url") or "").rstrip("/")
    topic = (cfg.get("topic") or "").strip("/")
    if not url or not topic:
        raise ValueError("Add your ntfy server address and topic first.")
    headers = {"Title": title.encode("utf-8").decode("latin-1", "ignore"), "Priority": priority, "Tags": "calendar"}
    if cfg.get("token"):
        headers["Authorization"] = f"Bearer {cfg['token']}"
    r = httpx.post(f"{url}/{topic}", content=body.encode("utf-8"), headers=headers, timeout=10)
    r.raise_for_status()


def _discord(cfg: dict, title: str, body: str) -> None:
    hook = (cfg.get("webhook") or "").strip()
    if not DISCORD_RX.match(hook):
        raise ValueError("That doesn't look like a Discord webhook address.")
    r = httpx.post(hook, json={"username": "Daybook", "embeds": [{"title": title[:250], "description": body[:2000], "color": 0x2B5F97}]},
                   timeout=10)
    r.raise_for_status()


def send(title: str, body: str, *, kind: str = "reminder", ref: str | None = None,
         redacted_title: str = "Daybook reminder", redacted_body: str | None = None) -> dict:
    cfg = db.get_settings()["notifications"]
    results: dict[str, str] = {}
    _store_inapp(title, body, kind, ref)
    results["inapp"] = "sent"
    if cfg.get("macos"):
        results["macos"] = "sent" if _macos(title, body) else "unavailable"
    redact = cfg.get("redact_external", True)
    ext_title = redacted_title if redact else title
    ext_body = (redacted_body or "Open Daybook for details.") if redact else body
    if cfg.get("ntfy", {}).get("enabled"):
        try:
            _ntfy(cfg["ntfy"], ext_title, ext_body)
            results["ntfy"] = "sent"
        except Exception as exc:  # network errors shouldn't stop other channels
            results["ntfy"] = f"failed: {exc}"
    if cfg.get("discord", {}).get("enabled"):
        try:
            _discord(cfg["discord"], ext_title, ext_body)
            results["discord"] = "sent"
        except Exception as exc:
            results["discord"] = f"failed: {exc}"
    return results


def test(channel: str) -> str:
    cfg = db.get_settings()["notifications"]
    title, body = "Daybook test", "Notifications are working."
    if channel == "inapp":
        _store_inapp(title, body, "test", None)
        return "Sent to every open Daybook window."
    if channel == "macos":
        if _macos(title, body):
            return "Sent to Notification Center on this Mac. If nothing appeared, allow notifications for Script Editor in System Settings → Notifications."
        raise ValueError("macOS notifications only work when Daybook runs on a Mac.")
    if channel == "ntfy":
        _ntfy(cfg["ntfy"], title, body)
        return "Sent to ntfy."
    if channel == "discord":
        _discord(cfg["discord"], title, body)
        return "Sent to Discord."
    raise ValueError("Unknown channel.")


def recent(limit: int = 30) -> list[dict]:
    rows = db.q("SELECT * FROM inbox_items ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["read"] = bool(r["read"])
    return rows


def mark_read() -> None:
    with db.tx() as c:
        c.execute("UPDATE inbox_items SET read = 1 WHERE read = 0")
