"""Backups, exports and restores.

* Automatic nightly snapshots of the database in <data>/backups (local only).
* Export everything as a ZIP of plain Markdown files (Obsidian-compatible, with
  front matter), attachments, calendar.ics and a full-fidelity daybook.json.
* Optional passphrase-encrypted export (AES-256-GCM).
* Restore from a Daybook export or from the previous version's JSON backup.
"""
from __future__ import annotations

import io
import json
import re
import sqlite3
import zipfile
from datetime import datetime
from pathlib import Path

from .. import __version__, db, security
from ..bus import changed
from ..util import jloads, new_id, now_ms, safe_filename
from . import attachments, converters, events as events_svc, notebooks as notebooks_svc, notes as notes_svc
from . import tasks as tasks_svc

LEGACY_SEED_IDS = {"evt-1", "evt-2", "evt-3", "note-1", "note-2", "note-3"}
LEGACY_SEED_TITLES = {
    "Design Sync & Feature Planning", "Discord Webhook Integration Test", "Tailscale Mobile App Verification",
    "Project Roadmap & Discord Integration", "Daily Meeting & Action Items", "Tailscale Mobile Configuration Guide",
}


# ---------------------------------------------------------------------------
# Automatic local snapshots
# ---------------------------------------------------------------------------
def snapshot() -> Path:
    p = db.get_paths()
    p.backups.mkdir(parents=True, exist_ok=True)
    dest = p.backups / f"daybook-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    src = sqlite3.connect(str(p.db))
    try:
        out = sqlite3.connect(str(dest))
        with out:
            src.backup(out)
        out.close()
    finally:
        src.close()
    keep = int(db.get_settings()["backups"].get("keep", 14))
    snaps = sorted(p.backups.glob("daybook-*.db"))
    for old in snaps[:-keep] if keep > 0 else []:
        old.unlink(missing_ok=True)
    db.update_settings({"backups": {"last": now_ms()}})
    return dest


def list_snapshots() -> list[dict]:
    p = db.get_paths()
    return [{"name": f.name, "size": f.stat().st_size, "created_at": int(f.stat().st_mtime * 1000)}
            for f in sorted(p.backups.glob("daybook-*.db"), reverse=True)]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def to_json() -> dict:
    data = {
        "format": "daybook-export",
        "version": __version__,
        "exported_at": now_ms(),
        "notebooks": db.q("SELECT * FROM notebooks"),
        "notes": db.q("SELECT id, title, body, notebook_id, tags, color, pinned, archived, trashed_at, linked_date, kind, "
                      "source_name, summary, locked, cipher, created_at, updated_at FROM notes"),
        "calendars": db.q("SELECT * FROM calendars"),
        "events": db.q("SELECT * FROM events"),
        "tasks": db.q("SELECT * FROM tasks"),
        "attachments": db.q("SELECT * FROM attachments"),
    }
    for n in data["notes"]:
        n["tags"] = jloads(n["tags"], [])
    return data


def _front_matter(n: dict, notebook: str | None) -> str:
    def esc(v: str) -> str:
        return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = ["---", f"title: {esc(n['title'] or 'Untitled')}", f"id: {n['id']}",
             f"created: {datetime.fromtimestamp(n['created_at'] / 1000).isoformat(timespec='seconds')}",
             f"updated: {datetime.fromtimestamp(n['updated_at'] / 1000).isoformat(timespec='seconds')}"]
    tags = n["tags"] if isinstance(n["tags"], list) else jloads(n["tags"], [])
    if tags:
        lines.append("tags: [" + ", ".join(tags) + "]")
    if notebook:
        lines.append(f"notebook: {esc(notebook)}")
    if n.get("linked_date"):
        lines.append(f"date: {n['linked_date']}")
    if n.get("pinned"):
        lines.append("pinned: true")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def export_zip() -> bytes:
    data = to_json()
    books = {b["id"]: b["name"] for b in data["notebooks"]}
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("daybook.json", json.dumps(data, indent=1, ensure_ascii=False))
        z.writestr("README.txt", "Daybook export.\n\nnotes/ holds every note as a Markdown file (open with any editor, "
                                 "or as an Obsidian vault). attachments/ holds files. calendar.ics imports into any calendar app.\n"
                                 "Locked notes stay encrypted in daybook.json and are not written as Markdown.\n")
        for n in data["notes"]:
            if n["trashed_at"] or n["locked"]:
                continue
            folder = safe_filename(books.get(n["notebook_id"], "") or "Unfiled")
            base = safe_filename(n["title"] or "Untitled")
            path = f"notes/{folder}/{base}.md"
            i = 2
            while path.lower() in used:
                path = f"notes/{folder}/{base} ({i}).md"
                i += 1
            used.add(path.lower())
            body = re.sub(r"\(/api/attachments/([A-Za-z0-9]+)\)", lambda m: f"(../../attachments/{m.group(1)})", n["body"])
            z.writestr(path, _front_matter(n, books.get(n["notebook_id"])) + body)
        for a in data["attachments"]:
            try:
                z.write(attachments.path_of(a), f"attachments/{a['id']}-{a['filename']}")
            except (OSError, PermissionError):
                continue
        z.writestr("calendar.ics", events_svc.export_ics())
        open_tasks = [t for t in data["tasks"] if not t["done"]]
        lines = ["# Tasks", ""] + [f"- [ ] {t['title']}" + (f" (due {t['due_date']})" if t["due_date"] else "") for t in open_tasks]
        lines += ["", "## Done", ""] + [f"- [x] {t['title']}" for t in data["tasks"] if t["done"]]
        z.writestr("tasks.md", "\n".join(lines) + "\n")
    return buf.getvalue()


def export_encrypted(passphrase: str) -> bytes:
    err = security.validate_new_passphrase(passphrase)
    if err:
        raise ValueError(err)
    return security.encrypt_bytes(export_zip(), passphrase)


def export_note_markdown(note_id: str) -> tuple[str, str]:
    row = notes_svc.get_row(note_id)
    if row["locked"]:
        raise notes_svc.Locked("Unlock the note before exporting it.")
    book = db.q1("SELECT name FROM notebooks WHERE id = ?", (row["notebook_id"],)) if row["notebook_id"] else None
    md = _front_matter({**row, "tags": jloads(row["tags"], [])}, book["name"] if book else None) + row["body"]
    return safe_filename(row["title"] or "Untitled") + ".md", md


# ---------------------------------------------------------------------------
# Restore / import
# ---------------------------------------------------------------------------
def import_payload(data: dict, attachment_files: dict[str, bytes] | None = None) -> dict:
    if data.get("format") != "daybook-export":
        if "notes" in data or "events" in data:
            return import_legacy(data)
        raise ValueError("This file isn't a Daybook export.")
    counts = {"notes": 0, "events": 0, "tasks": 0, "skipped": 0}
    for b in data.get("notebooks", []):
        if not db.q1("SELECT id FROM notebooks WHERE id = ? OR lower(name) = lower(?)", (b["id"], b["name"])):
            with db.tx() as c:
                c.execute("INSERT INTO notebooks(id, name, color, sort, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                          (b["id"], b["name"], b.get("color"), b.get("sort", 0), b.get("created_at", now_ms()), b.get("updated_at", now_ms())))
    for cal in data.get("calendars", []):
        if not db.q1("SELECT id FROM calendars WHERE id = ? OR lower(name) = lower(?)", (cal["id"], cal["name"])):
            with db.tx() as c:
                c.execute("INSERT INTO calendars(id, name, color, visible, sort, created_at) VALUES (?,?,?,?,?,?)",
                          (cal["id"], cal["name"], cal["color"], cal.get("visible", 1), cal.get("sort", 0), cal.get("created_at", now_ms())))
    book_ids = {r["id"] for r in db.q("SELECT id FROM notebooks")}
    for a in data.get("attachments", []):
        if db.q1("SELECT id FROM attachments WHERE id = ?", (a["id"],)):
            continue
        blob = (attachment_files or {}).get(a["id"])
        if blob is None:
            continue
        p = db.get_paths().attachments / a["rel_path"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(blob)
        with db.tx() as c:
            c.execute("INSERT INTO attachments(id, note_id, filename, mime, size, sha256, rel_path, role, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                      (a["id"], None, a["filename"], a.get("mime"), a.get("size", 0), a.get("sha256"), a["rel_path"], a.get("role", "inline"), a.get("created_at", now_ms())))
    for n in data.get("notes", []):
        if db.q1("SELECT id FROM notes WHERE id = ?", (n["id"],)):
            counts["skipped"] += 1
            continue
        with db.tx() as c:
            c.execute(
                """INSERT INTO notes(id, title, body, notebook_id, tags, color, pinned, archived, trashed_at, linked_date, kind,
                       source_name, summary, locked, cipher, word_count, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (n["id"], n.get("title", ""), n.get("body", ""), n.get("notebook_id") if n.get("notebook_id") in book_ids else None,
                 json.dumps(n.get("tags") or []), n.get("color"), int(bool(n.get("pinned"))), int(bool(n.get("archived"))),
                 n.get("trashed_at"), n.get("linked_date"), n.get("kind") or "note", n.get("source_name"), n.get("summary"),
                 int(bool(n.get("locked"))), n.get("cipher"), 0, n.get("created_at") or now_ms(), n.get("updated_at") or now_ms()),
            )
        counts["notes"] += 1
    with db.tx() as c:
        for a in data.get("attachments", []):
            if a.get("note_id"):
                c.execute("UPDATE attachments SET note_id = ? WHERE id = ? AND note_id IS NULL AND EXISTS (SELECT 1 FROM notes WHERE id = ?)",
                          (a["note_id"], a["id"], a["note_id"]))
    cal_ids = {r["id"] for r in db.q("SELECT id FROM calendars")}
    for e in data.get("events", []):
        if db.q1("SELECT id FROM events WHERE id = ?", (e["id"],)):
            counts["skipped"] += 1
            continue
        with db.tx() as c:
            c.execute(
                """INSERT INTO events(id, title, description, location, calendar_id, all_day, start, end, rrule, exdates, reminders,
                       note_id, uid, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (e["id"], e["title"], e.get("description", ""), e.get("location", ""), e.get("calendar_id") if e.get("calendar_id") in cal_ids else None,
                 int(bool(e.get("all_day"))), e["start"], e["end"], e.get("rrule"), e.get("exdates") or "[]", e.get("reminders") or "[]",
                 e.get("note_id") if e.get("note_id") and db.q1("SELECT id FROM notes WHERE id = ?", (e.get("note_id"),)) else None,
                 e.get("uid"), e.get("created_at") or now_ms(), e.get("updated_at") or now_ms()),
            )
        counts["events"] += 1
    for t in data.get("tasks", []):
        if db.q1("SELECT id FROM tasks WHERE id = ?", (t["id"],)):
            counts["skipped"] += 1
            continue
        with db.tx() as c:
            c.execute(
                """INSERT INTO tasks(id, title, details, list, priority, due_date, due_time, remind, done, done_at, note_id, sort, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (t["id"], t["title"], t.get("details", ""), t.get("list"), t.get("priority", 0), t.get("due_date"), t.get("due_time"),
                 t.get("remind"), int(bool(t.get("done"))), t.get("done_at"),
                 t.get("note_id") if t.get("note_id") and db.q1("SELECT id FROM notes WHERE id = ?", (t.get("note_id"),)) else None,
                 t.get("sort", 0), t.get("created_at") or now_ms(), t.get("updated_at") or now_ms()),
            )
        counts["tasks"] += 1
    # Refresh word counts and links for imported notes.
    from ..util import word_count

    with db.tx() as c:
        for n in db.q("SELECT id, body FROM notes WHERE word_count = 0 AND locked = 0"):
            c.execute("UPDATE notes SET word_count = ? WHERE id = ?", (word_count(n["body"]), n["id"]))
    for e in ("notes", "events", "tasks", "notebooks", "calendars"):
        changed(e)
    return counts


def import_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        if "daybook.json" not in z.namelist():
            raise ValueError("This ZIP isn't a Daybook export. Use Import to turn its files into notes instead.")
        data = json.loads(z.read("daybook.json"))
        files: dict[str, bytes] = {}
        for name in z.namelist():
            if name.startswith("attachments/") and not name.endswith("/"):
                aid = Path(name).name.split("-", 1)[0]
                files[aid] = z.read(name)
    return import_payload(data, files)


def import_encrypted(blob: bytes, passphrase: str) -> dict:
    raw = security.decrypt_bytes(blob, passphrase)
    tmp = db.get_paths().tmp / f"restore-{new_id()}.zip"
    tmp.write_bytes(raw)
    try:
        return import_zip(tmp)
    finally:
        tmp.unlink(missing_ok=True)


LEGACY_ALERT = {"at_time": 0, "15m": 15, "1h": 60, "1d": 1440}
LEGACY_COLORS = {"blue": "blue", "emerald": "green", "purple": "violet", "amber": "amber", "slate": None}


def import_legacy(data: dict, skip_samples: bool = True) -> dict:
    """Import the previous Calendar-Notes-Discord format (localStorage backup JSON)."""
    counts = {"notes": 0, "events": 0, "skipped": 0}
    events_svc.seed_calendars()
    for n in data.get("notes") or []:
        if skip_samples and n.get("id") in LEGACY_SEED_IDS and n.get("title") in LEGACY_SEED_TITLES:
            counts["skipped"] += 1
            continue
        html = n.get("htmlContent")
        body = converters.html_to_markdown(html) if html else (n.get("content") or "")
        ts = int(n.get("updatedAt") or now_ms())
        notes_svc.create(title=n.get("title") or "Untitled", body=body, tags=n.get("tags") or [],
                         linked_date=n.get("linkedDate"), pinned=bool(n.get("pinned")),
                         color=LEGACY_COLORS.get(n.get("color") or "slate"), created_at=ts, updated_at=ts, emit=False)
        counts["notes"] += 1
    for e in data.get("events") or []:
        if skip_samples and e.get("id") in LEGACY_SEED_IDS and e.get("title") in LEGACY_SEED_TITLES:
            counts["skipped"] += 1
            continue
        if not e.get("date") or not e.get("title"):
            counts["skipped"] += 1
            continue
        cat = (e.get("category") or "").strip().capitalize()
        cal = events_svc.calendar_by_name(cat) if cat else None
        all_day = bool(e.get("allDay")) or not e.get("startTime")
        start = e["date"] if all_day else f"{e['date']}T{e['startTime'][:5]}"
        end = e["date"] if all_day else f"{e['date']}T{(e.get('endTime') or e['startTime'])[:5]}"
        reminders = [LEGACY_ALERT.get(e.get("discordAlertTime"), 15)] if e.get("notifyDiscord") else []
        try:
            events_svc.create({"title": e["title"], "description": e.get("description") or "", "location": e.get("location") or "",
                               "calendar_id": cal["id"] if cal else None, "all_day": all_day, "start": start, "end": end,
                               "reminders": reminders}, emit=False)
            counts["events"] += 1
        except ValueError:
            counts["skipped"] += 1
    changed("notes")
    changed("events")
    db.update_settings({"migration": {"legacy_imported": True}})
    return counts


def restore_any(filename: str, blob: bytes, passphrase: str | None = None) -> dict:
    name = filename.lower()
    if name.endswith(".daybook") or blob[:4] == b"DBK1":
        if not passphrase:
            raise PermissionError("This backup is encrypted. Enter its passphrase.")
        return import_encrypted(blob, passphrase)
    if name.endswith(".zip"):
        tmp = db.get_paths().tmp / f"restore-{new_id()}.zip"
        tmp.write_bytes(blob)
        try:
            return import_zip(tmp)
        finally:
            tmp.unlink(missing_ok=True)
    if name.endswith(".json"):
        try:
            data = json.loads(blob.decode("utf-8-sig"))
        except ValueError as exc:
            raise ValueError("That JSON file couldn't be read.") from exc
        return import_payload(data)
    raise ValueError("Choose a Daybook export (.zip or .daybook) or a backup .json from the previous version.")


def seed_defaults() -> None:
    notebooks_svc.seed()
    events_svc.seed_calendars()
    _ = tasks_svc  # imported for side effects in callers
