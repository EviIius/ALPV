"""Finishing an update: noticing newer files, the start-up check, the in-place restart,
and the pieces that keep a phone and the server on the same version."""
from __future__ import annotations

import gzip
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx

import app
from app import runtime
from app.services import updates
from tests.test_daybook import H, client, mock_ai  # noqa: F401  (fixtures)
from tests.test_reader import connect, deck_file, lib  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def test_versions_agree():
    """The server, the pages and the service worker carry one version, so a mismatch means 'restart' or 'reload'."""
    page = re.search(r"VERSION = '([^']+)'", (ROOT / "app/static/js/version.js").read_text()).group(1)
    worker = re.search(r"VERSION = 'daybook-([^']+)'", (ROOT / "app/static/sw.js").read_text()).group(1)
    assert page == worker == app.__version__ == updates.disk_version()


def test_update_status_and_check(client, monkeypatch):
    h = client.get("/api/health").json()
    assert h["version"] == app.__version__ and h["restartable"] is False and "started_at" in h
    # Files newer than the running copy mean an update is waiting.
    monkeypatch.setattr(updates, "STARTED", time.time() + 3600)
    updates._cache["at"] = 0
    assert client.get("/api/system/update").json()["update_ready"] is False
    monkeypatch.setattr(updates, "STARTED", 0.0)
    assert client.get("/api/health").json()["update_ready"] is True
    assert client.get("/api/system").json()["update_ready"] is True

    # Not started by run.py (as here): it says so instead of pretending.
    r = client.post("/api/system/restart", headers=H)
    assert r.status_code == 409 and "can't restart itself" in r.json()["detail"]
    assert client.post("/api/system/restart").status_code == 403      # no header: refused

    # The start-up check passes on this code and leaves nothing behind.
    client.post("/api/notes", json={"title": "Kept", "body": "x"}, headers=H)
    check = updates.preflight()
    assert check == {"ok": True, "version": app.__version__, "message": "Ready."}
    from app import db
    assert not list(db.get_paths().tmp.glob("update-check-*"))

    # With a server handle, a passed check asks it to stop; run.py then starts the new copy.
    fake = SimpleNamespace(should_exit=False)
    monkeypatch.setattr(runtime, "server", fake)
    monkeypatch.setattr(runtime, "restart_requested", False)
    r = client.post("/api/system/restart", headers=H)
    assert r.status_code == 200 and r.json() == {"restarting": True, "from": app.__version__, "to": app.__version__}
    time.sleep(1.0)
    assert fake.should_exit is True and runtime.restart_requested is True
    assert client.post("/api/system/restart", headers=H).status_code == 409   # already restarting

    # A failed check keeps the running copy.
    monkeypatch.setattr(runtime, "restart_requested", False)
    fake.should_exit = False
    monkeypatch.setattr(updates, "preflight", lambda: {"ok": False, "version": None, "message": "SyntaxError: invalid syntax (app/x.py, line 1)"})
    r = client.post("/api/system/restart", headers=H)
    assert r.status_code == 409 and "kept running" in r.json()["detail"] and "app/x.py" in r.json()["detail"]
    time.sleep(0.8)
    assert fake.should_exit is False and runtime.restart_requested is False


def test_error_summary():
    err = 'Traceback (most recent call last):\n  File "<string>", line 7, in <module>\n  File "%s/app/services/x.py", line 3, in <module>\n    import nope\nModuleNotFoundError: No module named \'nope\'' % ROOT
    assert updates._tail(err) == "ModuleNotFoundError: No module named 'nope' (app/services/x.py, line 3)"
    assert updates._tail("") == ""


def test_restart_in_place(tmp_path):
    """Start the real server, restart it from the app, and find the same process back on the same port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "DAYBOOK_LIBRARY": ""}
    proc = subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--localhost-only", "--port", str(port), "--data", str(tmp_path / "data")],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        c = httpx.Client(base_url=base, headers={"X-Daybook": "1"}, timeout=120)
        first = None
        for _ in range(80):
            try:
                first = c.get("/api/health").json()
                break
            except httpx.HTTPError:
                time.sleep(0.25)
        assert first and first["restartable"] is True
        assert c.post("/api/auth/setup", json={"passphrase": "correct horse battery"}).status_code == 200
        note = c.post("/api/notes", json={"title": "Before the restart", "body": "still here"}).json()
        assert c.post("/api/system/restart").json()["restarting"] is True
        again = None
        for _ in range(120):
            time.sleep(0.25)
            try:
                h = httpx.get(base + "/api/health", timeout=2).json()
                if h["started_at"] != first["started_at"]:
                    again = h
                    break
            except httpx.HTTPError:
                continue
        assert again, "the server didn't come back"
        assert proc.poll() is None                                     # the same process, replaced in place
        assert c.get(f"/api/notes/{note['id']}").json()["title"] == "Before the restart"   # still signed in, data intact
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_slide_preview_is_light(client, lib):
    by = connect(client, lib)
    deck = by["Kelly/SAS_Reduction_Plan_DRAFT_v3.pptx"]
    full = client.get(f"/api/reader/{deck['id']}/slides")
    assert full.headers.get("content-encoding") == "gzip" and full.json()["count"] == len(full.json()["slides"]) >= 3
    peek = client.get(f"/api/reader/{deck['id']}/slides", params={"limit": 2}).json()
    assert len(peek["slides"]) == 2 and peek["count"] == full.json()["count"] and peek["width"] == full.json()["width"]
    assert set(peek["slides"][0]) == {"n", "title", "hidden", "html"} and "notes" in full.json()["slides"][0]
    raw = client.get(f"/api/reader/{deck['id']}/slides", headers={"Accept-Encoding": "identity"})
    assert "content-encoding" not in raw.headers and json.loads(raw.content)["count"] == peek["count"]
