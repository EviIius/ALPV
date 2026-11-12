"""Finishing an update without a trip to the computer Daybook runs on.

Daybook's files can change while it runs: a `git pull`, or an assistant writing a
new version into the folder. Pages are served from disk, so phones get the new
screens at once, but the server keeps running the code it started with until it
restarts. New screens then call parts of the server that aren't there yet.

This module notices that the code on disk is newer than the code that's running,
checks that the new code can start, and restarts the server in place: the same
process, window and address. Nothing here talks to the internet except `pip`,
and only when requirements.txt changed (exactly what start.sh does on start-up).
"""
from __future__ import annotations

import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .. import __version__, db, runtime
from ..config import BASE_DIR, STATIC_DIR

ROOT = BASE_DIR.parent
STARTED = time.time()
MAX_DB_COPY = 2 * 1024**3   # skip the database rehearsal above 2 GB
_cache: dict = {"at": 0.0, "newest": 0.0}
_restart_lock = threading.Lock()


class UpdateError(Exception):
    pass


def disk_version() -> str:
    """The version in app/__init__.py as it is on disk now."""
    try:
        m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', (BASE_DIR / "__init__.py").read_text("utf-8"), re.M)
        return m.group(1) if m else __version__
    except OSError:
        return __version__


def _newest_source() -> float:
    """When the server's own code last changed on disk (cached for a few seconds)."""
    now = time.time()
    if now - _cache["at"] < 5:
        return _cache["newest"]
    newest = 0.0
    files = [ROOT / "run.py", ROOT / "requirements.txt"]
    try:
        files += [p for p in BASE_DIR.rglob("*.py") if STATIC_DIR not in p.parents]
    except OSError:
        pass
    for p in files:
        try:
            newest = max(newest, p.stat().st_mtime)
        except OSError:
            continue
    _cache.update(at=now, newest=newest)
    return newest


def status() -> dict:
    dv = disk_version()
    return {
        "version": __version__,
        "disk_version": dv,
        # The files changed after this copy started, or say they're another version.
        "update_ready": dv != __version__ or _newest_source() > STARTED + 2,
        "restartable": runtime.server is not None,
        "started_at": int(STARTED * 1000),
    }


# ---------------------------------------------------------------------------
# Can the code on disk start?
# ---------------------------------------------------------------------------
_CHECK = r"""
import importlib, pkgutil, sys
sys.path.insert(0, sys.argv[1])
import run
run.check_dependencies()
import app
for m in pkgutil.walk_packages(app.__path__, "app."):
    importlib.import_module(m.name)
from app.main import create_app
create_app(background=False)
if len(sys.argv) > 2:
    from pathlib import Path
    from app import db
    from app.config import Paths
    db.configure(Paths(Path(sys.argv[2])))
    db.migrate()
print("daybook-check-ok", app.__version__)
"""


def _tail(text: str, limit: int = 400) -> str:
    """The useful end of a Python error: what went wrong and where."""
    lines = [ln.strip() for ln in (text or "").strip().splitlines() if ln.strip()]
    if not lines:
        return ""
    what = lines[-1]
    where = ""
    for ln in reversed(lines):
        m = re.match(r'File "(.+?)", line (\d+)', ln)
        if m and "importlib" not in m.group(1):
            try:
                name = str(Path(m.group(1)).resolve().relative_to(ROOT))
            except ValueError:
                name = Path(m.group(1)).name
            where = f" ({name}, line {m.group(2)})"
            break
    return (what + where)[-limit:]


def _install_requirements(timeout: int) -> str | None:
    """start.sh installs requirements when requirements.txt is newer than its marker; do the same."""
    venv = ROOT / ".venv"
    marker, req = venv / ".deps-ok", ROOT / "requirements.txt"
    try:
        in_venv = Path(sys.prefix).resolve() == venv.resolve()
        stale = marker.exists() and req.exists() and req.stat().st_mtime > marker.stat().st_mtime
    except OSError:
        return None
    if not (in_venv and stale):
        return None
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "-r", str(req)],
                           cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"Couldn't install the updated requirements ({exc.__class__.__name__})."
    if r.returncode != 0:
        return "Couldn't install the updated requirements. " + _tail(r.stderr or r.stdout)
    try:
        marker.touch()
    except OSError:
        pass
    return None


def preflight(timeout: int = 300) -> dict:
    """Rehearse starting the code on disk, without touching the running server or the real database.

    In a fresh Python running that code: check the requirements, import every module, build the
    app, and apply any pending database changes to a throwaway copy of the database.
    Returns {"ok": bool, "version": str | None, "message": str}."""
    problem = _install_requirements(timeout)
    if problem:
        return {"ok": False, "version": None, "message": problem}
    p = db.get_paths()
    work = None
    args = [sys.executable, "-c", _CHECK, str(ROOT)]
    try:
        try:
            if p.db.exists() and p.db.stat().st_size <= MAX_DB_COPY:
                p.tmp.mkdir(parents=True, exist_ok=True)
                work = Path(tempfile.mkdtemp(prefix="update-check-", dir=p.tmp))
                src = sqlite3.connect(f"file:{p.db}?mode=ro", uri=True, timeout=10)
                dst = sqlite3.connect(str(work / "daybook.db"))
                try:
                    src.backup(dst)
                finally:
                    dst.close()
                    src.close()
                args.append(str(work))
        except (OSError, sqlite3.Error) as exc:
            return {"ok": False, "version": None, "message": f"Couldn't copy the database to rehearse the update ({exc})."}
        try:
            r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "version": None, "message": f"The start-up check didn't finish ({exc.__class__.__name__})."}
        m = re.search(r"daybook-check-ok (\S+)", r.stdout or "")
        if r.returncode != 0 or not m:
            return {"ok": False, "version": None, "message": _tail(r.stderr) or _tail(r.stdout) or "The new code didn't start."}
        return {"ok": True, "version": m.group(1), "message": "Ready."}
    finally:
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


def restart() -> dict:
    """Check the code on disk, then restart into it. Raises UpdateError if it can't or shouldn't."""
    if runtime.server is None:
        raise UpdateError("Daybook wasn't started with Start Daybook.command (or run.py), so it can't restart itself. "
                          "Restart it on the computer it runs on.")
    if not _restart_lock.acquire(blocking=False):
        raise UpdateError("Daybook is already restarting.")
    try:
        if runtime.restart_requested:
            raise UpdateError("Daybook is already restarting.")
        check = preflight()
        if not check["ok"]:
            raise UpdateError("The updated files didn't pass the start-up check, so Daybook kept running as it was. "
                              + check["message"])
        runtime.restart_requested = True
        # Let this answer reach the browser, then ask the server to stop; run.py starts the new copy.
        threading.Timer(0.6, lambda: setattr(runtime.server, "should_exit", True)).start()
        return {"restarting": True, "from": __version__, "to": check["version"]}
    finally:
        _restart_lock.release()
