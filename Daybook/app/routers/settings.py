"""Settings, privacy check-up, notification tests, backups, export/restore."""
from __future__ import annotations

import platform
import sys
import time
from datetime import datetime

from fastapi import APIRouter, Body, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response

from .. import __version__, db, security
from ..bus import changed
from ..services import backup, notify, privacy, updates
from ..util import tzinfo

router = APIRouter(prefix="/api")
_STARTED = time.time()
MASK = "••••"
ALLOWED_TOP = {"profile", "appearance", "timezone", "calendar", "ai", "import", "notifications", "backups", "library", "search", "glossary"}


def _mask(value: str) -> str:
    return (MASK + value[-4:]) if value else ""


def _public(s: dict) -> dict:
    s = dict(s)
    s.pop("vault", None)
    s.pop("migration", None)
    if isinstance(s.get("feed"), dict):
        s["feed"] = {k: v for k, v in s["feed"].items() if k != "token"}  # the feed link is shown in Settings → Calendars
    n = s["notifications"]
    n["discord"] = {**n["discord"], "webhook": _mask(n["discord"].get("webhook", ""))}
    n["ntfy"] = {**n["ntfy"], "token": _mask(n["ntfy"].get("token", ""))}
    return s


def _strip_masked(patch: dict) -> dict:
    n = patch.get("notifications") or {}
    if isinstance(n.get("discord"), dict) and str(n["discord"].get("webhook", "")).startswith(MASK):
        n["discord"].pop("webhook")
    if isinstance(n.get("ntfy"), dict) and str(n["ntfy"].get("token", "")).startswith(MASK):
        n["ntfy"].pop("token")
    return patch


@router.get("/settings")
def get_settings():
    return _public(db.get_settings())


@router.put("/settings")
def put_settings(payload: dict = Body(...)):
    patch = {k: v for k, v in payload.items() if k in ALLOWED_TOP}
    patch = _strip_masked(patch)
    if "timezone" in patch:
        tz = str(patch["timezone"])
        if tzinfo(tz).key != tz:
            raise HTTPException(400, "That timezone isn't recognised. Use a name like America/New_York.")
    if "ai" in patch and "base_url" in patch["ai"]:
        url = str(patch["ai"]["base_url"]).strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(400, "The AI address should start with http:// or https://.")
        patch["ai"]["base_url"] = url.rstrip("/")
    disc = (patch.get("notifications") or {}).get("discord") or {}
    if disc.get("webhook") and not notify.DISCORD_RX.match(disc["webhook"].strip()):
        raise HTTPException(400, "That doesn't look like a Discord webhook address.")
    lib = patch.get("library")
    if isinstance(lib, dict) and lib.get("path"):
        from pathlib import Path

        raw = str(lib["path"]).strip()
        p = Path(raw).expanduser()
        if not p.is_dir():
            raise HTTPException(400, f"Daybook can't find the folder {raw}.")
        p = p.resolve()
        if p in (Path("/"), Path.home().resolve()) or len(p.parts) < 3:
            raise HTTPException(400, "Pick a specific folder (like ~/Documents/GitHub/ALPV), not your whole drive or home folder.")
        if p == db.get_paths().data.resolve() or db.get_paths().data.resolve() in p.parents:
            raise HTTPException(400, "That's Daybook's own data folder.")
        lib["path"] = str(p)
    if "search" in patch:
        saved = (patch["search"] or {}).get("saved")
        if saved is not None:
            if not isinstance(saved, list):
                raise HTTPException(400, "Saved searches should be a list.")
            patch["search"]["saved"] = [{"name": str(x.get("name", ""))[:80], "q": str(x.get("q", ""))[:400]}
                                        for x in saved[:40] if isinstance(x, dict) and x.get("name") and x.get("q")]
    s = db.update_settings(patch)
    changed("settings")
    if "library" in patch:
        import threading

        from ..services.library import engine

        threading.Thread(target=engine.scan, kwargs={"full": True}, daemon=True).start()
    return _public(s)


@router.get("/privacy")
def privacy_checkup(request: Request):
    kind = security.classify_ip(request.state.client_ip)
    return {"checks": privacy.audit(kind, request.state.scheme), **privacy.summary()}


@router.post("/notifications/test")
def test_notification(payload: dict = Body(...)):
    try:
        return {"message": notify.test(payload.get("channel", "inapp"))}
    except ValueError:
        raise
    except Exception as exc:
        raise HTTPException(502, f"Couldn't send: {exc}") from exc


@router.get("/system")
def system():
    p = db.get_paths()
    return {
        "version": __version__, "python": sys.version.split()[0], "platform": platform.platform(),
        "data_dir": str(p.data), "db_bytes": p.db.stat().st_size if p.db.exists() else 0,
        "uptime_s": int(time.time() - _STARTED), "fts": db.fts_available(),
        **{k: v for k, v in updates.status().items() if k in ("disk_version", "update_ready", "restartable")},
    }


# Backups & export ---------------------------------------------------------------
@router.get("/backup/snapshots")
def snapshots():
    return backup.list_snapshots()


@router.post("/backup/snapshot")
def make_snapshot():
    path = backup.snapshot()
    return {"name": path.name}


def _stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


@router.get("/export/zip")
def export_zip():
    return Response(backup.export_zip(), media_type="application/zip",
                    headers={"Content-Disposition": f"attachment; filename=daybook-export-{_stamp()}.zip"})


@router.post("/export/encrypted")
def export_encrypted(payload: dict = Body(...)):
    blob = backup.export_encrypted(payload.get("passphrase") or "")
    return Response(blob, media_type="application/octet-stream",
                    headers={"Content-Disposition": f"attachment; filename=daybook-export-{_stamp()}.daybook"})


@router.post("/restore")
async def restore(file: UploadFile = File(...), passphrase: str | None = Form(None)):
    blob = await file.read()
    return backup.restore_any(file.filename or "backup", blob, passphrase)


@router.post("/restore/legacy")
def restore_legacy(payload: dict = Body(...)):
    return backup.import_legacy(payload, skip_samples=payload.get("skip_samples", True))


@router.get("/migration")
def migration_state():
    return db.get_settings()["migration"]
