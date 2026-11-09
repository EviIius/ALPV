"""The Library: a folder of your own files that Daybook reads in place.

Point Daybook at a folder (ALPV) and every file in it becomes a note, without
copying or moving anything:

* Markdown (.md) notes are fully editable. Daybook writes your edits straight
  back to the file, and edits made elsewhere (VS Code, Claude, a git pull) show
  up in Daybook within seconds.
* Word, PowerPoint, PDF, Excel, email and text files are converted on this
  computer into readable, searchable notes. The originals stay where they are.
  Open them in Word or PowerPoint from Daybook to edit them.
* HTML mockups open in a locked-down preview that can't reach the internet.
* Every file is dated by the date scrubber (see datescan.py) so it lands on the
  right day of the calendar.

Each top-level folder becomes a notebook. Daybook's own bookkeeping lives in
``<library>/.daybook/``: ``library.json`` marks the folder, ``dates.json``
holds dates chosen by you, the local AI or Claude, and ``trash/`` keeps files
you delete from Daybook so they can be restored.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from .. import db
from ..bus import bus, changed
from ..config import BASE_DIR, local_timezone_name
from ..util import jloads, md_to_text, new_id, now_ms, safe_filename, today_str
from . import attachments, converters, datescan, meeting

log = logging.getLogger("daybook")

APP_DIR = BASE_DIR.parent                 # …/ALPV/Daybook when merged into a library
CONVERTER_VERSION = 3                     # bump to re-convert every file
MAX_FILE_BYTES = 150 * 1024 * 1024
SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "dist", "build", "target", "Daybook-data"}
SKIP_PREFIXES = (".", "~$", "._", ".~lock")
SKIP_SUFFIXES = (".tmp", ".crdownload", ".part", ".download", ".icloud", ".lnk", ".lock", ".swp", ".pyc", ".db",
                 ".db-wal", ".db-shm", ".sqlite3", ".daybook")

KINDS: list[tuple[str, set[str]]] = [
    ("markdown", set(converters.MARKDOWN)),
    ("text", set(converters.TEXT)),
    ("document", set(converters.WORD) | set(converters.LEGACY_DOC)),
    ("pdf", set(converters.PDF)),
    ("deck", set(converters.SLIDES)),
    ("sheet", set(converters.SHEETS) | set(converters.DELIMITED)),
    ("mockup", set(converters.HTML)),
    ("email", set(converters.EMAIL) | set(converters.OUTLOOK)),
    ("image", set(converters.IMAGES)),
    ("code", set(converters.CODE)),
]
KIND_LABELS = {
    "markdown": "Note", "text": "Text", "transcript": "Recording transcript", "document": "Word document",
    "pdf": "PDF", "deck": "PowerPoint deck", "sheet": "Spreadsheet", "mockup": "Mockup", "email": "Email",
    "image": "Image", "code": "Code",
}
EDITABLE = {"markdown"}
FACT_KINDS = {"markdown", "document", "pdf", "text", "email"}


class LibraryError(Exception):
    pass


# ---------------------------------------------------------------------------
# Where the library is
# ---------------------------------------------------------------------------
def classify(name: str) -> str | None:
    ext = Path(name).suffix.lower()
    for kind, exts in KINDS:
        if ext in exts:
            if kind == "text" and _looks_like_recording(Path(name).stem):
                return "transcript"
            return kind
    return None


def _looks_like_recording(stem: str) -> bool:
    if re.search(r"transcript|recording|voice ?memo|dictation", stem, re.I):
        return True
    return any(f.time for f in datescan.find_dates(stem, filename=True))


def _skip_name(name: str) -> bool:
    low = name.lower()
    return name.startswith(SKIP_PREFIXES) or low.endswith(SKIP_SUFFIXES) or low in ("thumbs.db", "desktop.ini")


def _is_app_dir(p: Path) -> bool:
    return (p / "run.py").is_file() and (p / "app" / "main.py").is_file()


def auto_root() -> Path | None:
    """When Daybook lives inside a library (ALPV/Daybook), that library."""
    parent = APP_DIR.parent
    return parent if datescan.is_library(parent) else None


def configured_root() -> Path | None:
    s = db.get_settings()["library"]
    if not s.get("enabled", True):
        return None
    raw = os.environ.get("DAYBOOK_LIBRARY") or s.get("path") or ""
    if raw:
        p = Path(raw).expanduser()
        return p.resolve() if p.is_dir() else None
    p = auto_root()
    return p.resolve() if p else None


def ensure_marker(root: Path) -> dict:
    cfg_path = root / datescan.SIDECAR_DIR / datescan.CONFIG_FILE
    cfg = datescan.load_config(root)
    if not cfg:
        cfg = {
            "version": 1,
            "name": root.name,
            "timezone": db.get_settings().get("timezone") or local_timezone_name(),
            "about": "This folder is a Daybook library. Daybook reads the files here in place; nothing is copied "
                     "out. dates.json holds the day each file belongs to; trash/ keeps files deleted from Daybook.",
        }
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", "utf-8")
    gi = root / datescan.SIDECAR_DIR / ".gitignore"
    if not gi.exists():
        gi.write_text("# Files deleted from Daybook wait here; keep them out of git.\ntrash/\n*.tmp\n", "utf-8")
    return cfg


def safe_rel(root: Path, rel: str) -> Path:
    """Resolve a library-relative path, refusing anything that escapes the folder."""
    if not rel or rel.startswith(("/", "\\")) or "\x00" in rel:
        raise PermissionError("Not a library path.")
    target = (root / rel).resolve()
    if target != root and root not in target.parents:
        raise PermissionError("Not a library path.")
    parts = Path(rel).parts
    if any(p.startswith(".") for p in parts[:-1]) or parts[0] in (".git", datescan.SIDECAR_DIR):
        raise PermissionError("Not a library path.")
    return target


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{new_id(6)}.tmp")
    mode = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        pass
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        cand = path.with_name(f"{path.stem} {i}{path.suffix}")
        if not cand.exists():
            return cand
        i += 1


FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.S)


def split_front_matter(text: str) -> tuple[str, dict, str]:
    """(raw front matter including the --- lines, parsed keys, body)."""
    m = FRONT_MATTER.match(text)
    if not m:
        return "", {}, text
    keys = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.startswith((" ", "\t", "-")):
            k, v = line.split(":", 1)
            keys[k.strip().lower()] = v.strip().strip("'\"")
    return m.group(0), keys, text[m.end():]


def file_url(rel: str) -> str:
    return "/api/library/file?path=" + quote(rel)


# ---------------------------------------------------------------------------
# Notebooks <-> top-level folders
# ---------------------------------------------------------------------------
FOLDER_COLORS = ["#2B5F97", "#1F7A72", "#9A5B13", "#6A4C9C", "#A33A2B", "#3F6E3A", "#56665C", "#7A4E6B"]


def notebook_for(folder: str, root_name: str) -> str:
    """The notebook for a top-level folder ('' means files at the top level)."""
    row = db.q1("SELECT id FROM notebooks WHERE lib_dir = ?", (folder,))
    if row:
        return row["id"]
    name = folder or root_name
    existing = db.q1("SELECT id, lib_dir FROM notebooks WHERE lower(name) = lower(?)", (name,))
    if existing and existing["lib_dir"] is None:
        name = f"{name} ({root_name})"
    nid = new_id()
    n = db.scalar("SELECT count(*) FROM notebooks WHERE lib_dir IS NOT NULL") or 0
    with db.tx() as c:
        c.execute("INSERT INTO notebooks(id, name, color, sort, created_at, updated_at, lib_dir) VALUES (?,?,?,?,?,?,?)",
                  (nid, name, FOLDER_COLORS[n % len(FOLDER_COLORS)], -100 + n, now_ms(), now_ms(), folder))
    changed("notebooks", nid)
    return nid


def folder_of(rel: str) -> str:
    parts = rel.split("/")
    return parts[0] if len(parts) > 1 else ""


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------
@dataclass
class ScanContext:
    root: Path
    tz: str
    sidecar: dict
    git: dict
    clustered: set
    templates: set
    first: bool
    force: bool = False
    created: list = field(default_factory=list)


class Library:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.state: dict = {"configured": False, "scanning": False, "last_scan": 0, "error": None, "progress": None}
        self._root_seen: Path | None = None
        self._git = {"at": 0.0, "head": None, "dates": {}}
        self._sidecar_mtime = None
        self._scanned_once = False

    # -- helpers ------------------------------------------------------------
    def root(self) -> Path | None:
        return configured_root()

    def _git_dates(self, root: Path, force: bool = False) -> dict:
        head_file = root / ".git" / "HEAD"
        try:
            head = (root / ".git").stat().st_mtime if head_file.exists() else None
            ref = head_file.read_text().strip() if head_file.exists() else ""
            if ref.startswith("ref:"):
                rp = root / ".git" / ref[4:].strip()
                head = (head, rp.stat().st_mtime if rp.exists() else None)
        except OSError:
            head = None
        if force or head != self._git["head"] or time.time() - self._git["at"] > 600:
            dates = datescan.git_added_dates(root)
            self._git = {"at": time.time(), "head": head, "dates": {k: v for k, v in dates.items()}}
        return self._git["dates"]

    def _walk(self, root: Path) -> dict[str, tuple[int, int, float]]:
        out: dict[str, tuple[int, int, float]] = {}
        for dirpath, dirnames, filenames in os.walk(root):
            base = Path(dirpath)
            keep = []
            for d in dirnames:
                if d.startswith(".") or d in SKIP_DIRS:
                    continue
                full = base / d
                if full.is_symlink() or _is_app_dir(full):
                    continue
                keep.append(d)
            dirnames[:] = sorted(keep)
            for f in filenames:
                if _skip_name(f) or classify(f) is None:
                    continue
                p = base / f
                try:
                    st = p.lstat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE_BYTES:
                    continue
                out[p.relative_to(root).as_posix()] = (st.st_size, st.st_mtime_ns, st.st_mtime)
        return out

    def _context(self, root: Path, files: dict | None = None, *, first: bool = False) -> ScanContext:
        cfg = datescan.load_config(root)
        tz = cfg.get("timezone") or db.get_settings().get("timezone") or local_timezone_name()
        rows = db.q("SELECT path, mtime_ns, meta, status FROM lib_files")
        mtimes = {r["path"]: r["mtime_ns"] / 1e9 for r in rows if r["status"] == "ok"}
        if files:
            mtimes.update({k: v[2] for k, v in files.items()})
        metas = {r["path"]: jloads(r["meta"], {}).get("props", {}) for r in rows}
        return ScanContext(root=root, tz=tz, sidecar=datescan.load_sidecar(root)["dates"], git=self._git_dates(root),
                           clustered=datescan.clustered_mtimes(mtimes), templates=datescan.template_fingerprints(metas),
                           first=first)

    def _publish(self, **extra) -> None:
        self.state.update(extra)
        bus.publish({"type": "library", "state": self.public_state()})

    def public_state(self) -> dict:
        return {k: self.state.get(k) for k in ("configured", "scanning", "last_scan", "error", "progress", "root", "name")}

    # -- scanning -----------------------------------------------------------
    def scan(self, full: bool = False, wait: bool = True) -> dict:
        if not self.scan_lock.acquire(blocking=wait):
            return {"busy": True}
        try:
            return self._scan(full)
        finally:
            self.scan_lock.release()

    def poll(self) -> dict:
        """Called every few seconds by the scheduler; skips if a scan is already running."""
        return self.scan(full=False, wait=False)

    def _scan(self, full: bool = False) -> dict:
        root = self.root()
        if root is None:
            if self.state.get("configured"):
                self._publish(configured=False, root=None, name=None)
            self.state.update(configured=False, root=None, name=None)
            return {"configured": False}
        try:
            cfg = ensure_marker(root)
        except OSError as exc:
            self._publish(configured=True, root=str(root), error=f"Can't write to {root}: {exc.strerror or exc}")
            return {"configured": True, "error": self.state["error"]}
        first = root != self._root_seen
        if first:
            self._root_seen = root
            full = True
        self.state.update(configured=True, root=str(root), name=cfg.get("name") or root.name)
        try:
            files = self._walk(root)
        except OSError as exc:
            self._publish(error=f"Can't read {root}: {exc.strerror or exc}")
            return {"error": self.state["error"]}
        rows = {r["path"]: r for r in db.q("SELECT * FROM lib_files")}
        sc_mtime = None
        try:
            sc_mtime = datescan.sidecar_path(root).stat().st_mtime_ns
        except OSError:
            pass
        sidecar_changed = sc_mtime != self._sidecar_mtime
        todo, moved, missing = [], [], []
        for rel, (size, mns, _m) in files.items():
            r = rows.get(rel)
            if r is None or r["status"] in ("missing", "trashed") or r["size"] != size or r["mtime_ns"] != mns:
                todo.append(rel)
        for rel, r in rows.items():
            if rel not in files and r["status"] == "ok":
                missing.append(rel)
        # A new path whose content matches a vanished one is a move/rename.
        if missing and todo:
            gone = {rows[m]["sha256"]: m for m in missing if rows[m]["sha256"]}
            for rel in list(todo):
                if rel in rows:
                    continue
                try:
                    sha = _sha((root / rel).read_bytes())
                except OSError:
                    continue
                if sha in gone:
                    moved.append((gone.pop(sha), rel))
                    todo.remove(rel)
                    missing.remove(moved[-1][0])
        stale = []
        if not full:
            stale = [r["path"] for r in rows.values() if r["status"] == "ok" and r["path"] in files and r["path"] not in todo
                     and jloads(r["meta"], {}).get("cv") != CONVERTER_VERSION]
        if not (todo or moved or missing or stale or full or sidecar_changed):
            self.state["last_scan"] = now_ms()
            return {"changed": 0}

        ctx = self._context(root, files, first=first)
        self._sidecar_mtime = sc_mtime
        total = len(todo) + len(stale)
        self._publish(scanning=True, error=None, progress={"done": 0, "total": total} if total else None)
        touched = 0
        try:
            for old, new in moved:
                self._move_row(root, old, new, ctx)
                touched += 1
            for i, rel in enumerate(todo + stale):
                try:
                    self.index_file(root, rel, ctx, new_file=(rel not in rows and not first and self._scanned_once))
                except Exception as exc:  # one bad file must never stop the library
                    log.exception("Library: couldn't index %s", rel)
                    self._record_error(rel, f"{exc.__class__.__name__}: {exc}")
                touched += 1
                if total > 3 and (i % 4 == 3 or i == total - 1):
                    self._publish(progress={"done": i + 1, "total": total, "file": rel})
                    changed("notes")
            for rel in missing:
                self._mark_missing(rel)
                touched += 1
            if full or sidecar_changed or todo or moved:
                # Template fingerprints and file-time clusters depend on every file, so re-date with fresh context.
                touched += self.redecide_all(self._context(root, files, first=first))
        finally:
            self._scanned_once = True
            self._publish(scanning=False, progress=None, last_scan=now_ms())
        if touched:
            changed("notes")
            changed("library")
        return {"changed": touched, "indexed": len(todo), "moved": len(moved), "missing": len(missing)}

    def _record_error(self, rel: str, message: str) -> None:
        """Remember a failure (with the file's current size and time, so it isn't retried until it changes)."""
        size = mns = 0
        root = self.root()
        if root:
            try:
                st = (root / rel).lstat()
                size, mns = st.st_size, st.st_mtime_ns
            except OSError:
                pass
        with db.tx() as c:
            c.execute(
                """INSERT INTO lib_files(path, kind, size, mtime_ns, status, error, meta, first_seen, indexed_at)
                   VALUES (?,?,?,?,'error',?,'{}',?,?)
                   ON CONFLICT(path) DO UPDATE SET status = 'error', error = excluded.error, size = excluded.size,
                     mtime_ns = excluded.mtime_ns, indexed_at = excluded.indexed_at""",
                (rel, classify(rel) or "other", size, mns, message[:500], now_ms(), now_ms()))

    # -- one file -----------------------------------------------------------
    def index_file(self, root: Path, rel: str, ctx: ScanContext, *, new_file: bool = False) -> str | None:
        with self.lock:
            path = root / rel
            try:
                st = path.lstat()
            except OSError:
                return None
            row = db.q1("SELECT * FROM lib_files WHERE path = ?", (rel,))
            meta_prev = jloads(row["meta"], {}) if row else {}
            if (row and row["status"] == "ok" and row["size"] == st.st_size and row["mtime_ns"] == st.st_mtime_ns
                    and meta_prev.get("cv") == CONVERTER_VERSION and not ctx.force):
                return row["note_id"]
            data = path.read_bytes()
            sha = _sha(data)
            kind = classify(path.name) or "other"
            note_row = db.q1("SELECT * FROM notes WHERE id = ?", (row["note_id"],)) if row and row["note_id"] else None
            if (row and note_row and row["sha256"] == sha and meta_prev.get("cv") == CONVERTER_VERSION
                    and row["status"] in ("ok", "missing")):
                with db.tx() as c:
                    c.execute("UPDATE lib_files SET size = ?, mtime_ns = ?, status = 'ok', error = NULL, indexed_at = ? WHERE path = ?",
                              (st.st_size, st.st_mtime_ns, now_ms(), rel))
                    if row["status"] == "missing":
                        c.execute("UPDATE notes SET trashed_at = NULL WHERE id = ?", (row["note_id"],))
                return row["note_id"]

            title, body, fm, props, assets, warnings, conv_meta = self._convert(path, rel, kind, data, ctx)
            nb = notebook_for(folder_of(rel), ctx.root.name)
            ts_file = int(st.st_mtime * 1000)
            git_when = ctx.git.get(rel)
            created_ts = int(git_when.timestamp() * 1000) if git_when else ts_file
            if note_row is None:
                from . import notes as notes_svc

                note = notes_svc.create(title=title, body=body, notebook_id=nb, kind="note" if kind in EDITABLE else "library",
                                        source_name=path.name, created_at=created_ts, updated_at=ts_file, emit=False,
                                        skip_dating=True)
                note_id = note["id"]
            else:
                note_id = note_row["id"]
                with db.tx() as c:
                    if kind in EDITABLE and note_row["body"] != body and (note_row["body"] or "").strip():
                        c.execute("INSERT INTO note_revisions(note_id, title, body, created_at) VALUES (?,?,?,?)",
                                  (note_id, note_row["title"], note_row["body"], now_ms()))
                    c.execute(
                        """UPDATE notes SET title = ?, body = ?, notebook_id = ?, kind = ?, source_name = ?, word_count = ?,
                               trashed_at = NULL, updated_at = ?, rev = rev + 1 WHERE id = ?""",
                        (title, body, nb, "note" if kind in EDITABLE else "library", path.name,
                         len(re.findall(r"[\w'’-]+", md_to_text(body))), ts_file, note_id))
                # Old converted images go; the new conversion brings its own.
                for a in db.q("SELECT id FROM attachments WHERE note_id = ? AND role IN ('inline', 'file')", (note_id,)):
                    if kind not in EDITABLE:
                        attachments.delete(a["id"])
            if assets:
                body = self._store_assets(note_id, body, assets)
                with db.tx() as c:
                    c.execute("UPDATE notes SET body = ? WHERE id = ?", (body, note_id))
            meta = {"cv": CONVERTER_VERSION, "props": props, "kind": kind, "ext": path.suffix.lower(),
                    "warnings": warnings[:3], "fm": fm, **conv_meta}
            with db.tx() as c:
                c.execute(
                    """INSERT INTO lib_files(path, note_id, kind, size, mtime_ns, sha256, synced_hash, status, error, meta, first_seen, indexed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(path) DO UPDATE SET note_id = excluded.note_id, kind = excluded.kind, size = excluded.size,
                         mtime_ns = excluded.mtime_ns, sha256 = excluded.sha256, synced_hash = excluded.synced_hash,
                         status = excluded.status, error = excluded.error, meta = excluded.meta, indexed_at = excluded.indexed_at""",
                    (rel, note_id, kind, st.st_size, st.st_mtime_ns, sha, sha if kind in EDITABLE else None, "ok",
                     "; ".join(warnings[:2]) or None, json.dumps(meta), now_ms(), now_ms()))
                c.execute("UPDATE notes SET lib_path = ?, lib_kind = ? WHERE id = ?", (rel, kind, note_id))
            self.apply_dates(note_id, ctx=ctx, new_file=new_file)
            return note_id

    def _convert(self, path: Path, rel: str, kind: str, data: bytes, ctx: ScanContext):
        """(title, markdown body, front matter keys, document properties, assets, warnings, extra meta)."""
        from .ocr import make_ocr

        settings = db.get_settings()
        props: dict = {}
        fm: dict = {}
        assets: list = []
        warnings: list[str] = []
        extra: dict = {}
        stem_title = datescan.clean_title(path.stem, kind=kind)
        if kind == "markdown":
            text = data.decode("utf-8", "replace").replace("\r\n", "\n")
            _raw, fm, body = split_front_matter(text)
            return (fm.get("title") or stem_title)[:300], body, fm, {}, [], [], {}
        try:
            _t, props = datescan.quick_text(path, max_chars=2000) if kind in ("document", "deck", "sheet", "pdf", "email", "mockup") else ("", {})
        except Exception:
            props = {}
        if kind == "image":
            body = f"![{stem_title}]({file_url(rel)})\n"
            ocr = make_ocr(settings)
            if ocr is not None:
                try:
                    text = ocr(data, path.name)
                    if text and text.strip():
                        body += "\n## Text in this image\n\n" + converters.tidy_markdown(text)
                except Exception:
                    pass
            return stem_title, body, {}, props, [], [], {}
        try:
            conv = converters.convert(path, path.name, ocr=make_ocr(settings),
                                      max_rows=int(settings["import"].get("max_table_rows", 300)))
            body = conv.markdown
            assets = conv.assets
            warnings = conv.warnings
            extra = {k: v for k, v in (conv.meta or {}).items() if isinstance(v, (int, float, str))}
            if props.get("title") and kind != "mockup":
                extra["doc_title"] = props["title"]
            if conv.title and kind == "mockup":
                extra["doc_title"] = conv.title
                if re.fullmatch(r"(?:index|untitled|mockup|page|preview|prototype|design|draft)?[\s_-]*(?:\(?\d+\)?)?", path.stem, re.I) \
                        or len(conv.title) > len(stem_title):
                    stem_title = conv.title
        except converters.ConversionError as exc:
            body = f"*Daybook couldn't read this file: {exc}*\n"
            warnings = [str(exc)]
        return stem_title[:300], body, fm, props, assets, warnings, extra

    def _store_assets(self, note_id: str, body: str, assets: list) -> str:
        for i, a in enumerate(assets):
            row = attachments.save(a.data, a.name, mime=a.mime, note_id=note_id,
                                   role="inline" if a.mime.startswith("image/") else "file")
            body = body.replace(f"asset://{i})", f"{attachments.url(row['id'])})")
        return body

    def _move_row(self, root: Path, old: str, new: str, ctx: ScanContext) -> None:
        with self.lock:
            row = db.q1("SELECT * FROM lib_files WHERE path = ?", (old,))
            if not row:
                return
            st = (root / new).lstat()
            nb = notebook_for(folder_of(new), ctx.root.name)
            kind = classify(new) or row["kind"]
            with db.tx() as c:
                c.execute("UPDATE lib_files SET path = ?, size = ?, mtime_ns = ?, status = 'ok', kind = ?, indexed_at = ? WHERE path = ?",
                          (new, st.st_size, st.st_mtime_ns, kind, now_ms(), old))
                if row["note_id"]:
                    title = datescan.clean_title(Path(new).stem, kind=kind)
                    c.execute("UPDATE notes SET lib_path = ?, notebook_id = ?, title = ?, source_name = ?, trashed_at = NULL WHERE id = ?",
                              (new, nb, title, Path(new).name, row["note_id"]))
            if old in ctx.sidecar:
                datescan.rename_entry(root, old, new)
                ctx.sidecar[new] = ctx.sidecar.pop(old)
            if row["note_id"]:
                self.apply_dates(row["note_id"], ctx=ctx)
                changed("notes", row["note_id"])

    def _mark_missing(self, rel: str) -> None:
        with self.lock:
            row = db.q1("SELECT * FROM lib_files WHERE path = ?", (rel,))
            if not row:
                return
            with db.tx() as c:
                c.execute("UPDATE lib_files SET status = 'missing', indexed_at = ? WHERE path = ?", (now_ms(), rel))
                if row["note_id"]:
                    c.execute("UPDATE notes SET trashed_at = ? WHERE id = ? AND trashed_at IS NULL", (now_ms(), row["note_id"]))
            if row["note_id"]:
                changed("notes", row["note_id"])

    # -- dates ----------------------------------------------------------------
    def apply_dates(self, note_id: str, *, ctx: ScanContext | None = None, new_file: bool = False,
                    light: bool = False) -> dict | None:
        """Work out the note's date from every clue, store it, and refresh facts and mentions."""
        note = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
        if not note or note["locked"]:
            return None
        lf = db.q1("SELECT * FROM lib_files WHERE note_id = ?", (note_id,)) if note["lib_path"] else None
        settings = db.get_settings()
        tz = settings.get("timezone") or local_timezone_name()
        info_prev = jloads(note["date_info"], {}) or {}
        kind = note["lib_kind"] or ""
        if note["kind"] == "daily":
            return None
        if lf:
            root = Path(ctx.root) if ctx else self.root()
            if root is None:
                return None
            if ctx is None:
                ctx = self._context(root)
            meta = jloads(lf["meta"], {})
            props = dict(meta.get("props") or {})
            if meta.get("doc_title"):
                props["title"] = meta["doc_title"]
            rel = lf["path"]
            dec = datescan.decide(
                name=Path(rel).name, text=note["body"] or "", meta=props, frontmatter=meta.get("fm") or {},
                git_added=ctx.git.get(rel), mtime=lf["mtime_ns"] / 1e9 if lf["mtime_ns"] else None,
                mtime_trusted=rel not in ctx.clustered, new_file=new_file or bool(info_prev.get("new_file")),
                templates=ctx.templates, tz_name=ctx.tz, sidecar=ctx.sidecar.get(rel), kind=kind,
                created_ms=note["created_at"] if meta.get("made_here") else None,
            )
            if new_file:
                info_prev["new_file"] = True
        else:
            if note["date_status"] == "manual":
                dec = None
            else:
                use_created = settings["library"].get("date_new_notes", True) and note["kind"] in ("note", "ai")
                dec = datescan.decide(name=note["source_name"] or "", text=note["body"] or "", title=note["title"] or "",
                                      created_ms=note["created_at"] if use_created else None, tz_name=tz)
        ref = None
        if dec is not None:
            info = {
                "evidence": dec.evidence,
                "candidates": dec.candidates[:6],
                "conflict": dec.conflict,
                "suggestion": ({"date": (dec.candidates[0]["date"] if dec.candidates else None),
                                "end": dec.end, "time": dec.time, "confidence": dec.confidence,
                                "source": dec.candidates[0]["source"] if dec.candidates else None}
                               if dec.status in ("suggested", "none") and dec.candidates else None),
            }
            for keep in ("ai", "new_file"):
                if info_prev.get(keep):
                    info[keep] = info_prev[keep]
            on_cal = dec.status in ("set", "review", "manual") and dec.date
            with db.tx() as c:
                c.execute(
                    """UPDATE notes SET linked_date = ?, date_end = ?, date_time = ?, date_source = ?, date_confidence = ?,
                           date_status = ?, date_info = ? WHERE id = ?""",
                    (dec.date if on_cal else None, dec.end if on_cal else None, dec.time if on_cal else None,
                     dec.source, dec.confidence, dec.status, json.dumps(info), note_id))
            ref = date.fromisoformat(dec.date) if dec.date else None
            ment = dec.mentions if lf else datescan.mentions(note["body"] or "", ref or date.today(), dec.date)
        else:
            ref = date.fromisoformat(note["linked_date"]) if note["linked_date"] else None
            ment = datescan.mentions(note["body"] or "", ref or date.today(), note["linked_date"])
        if light and dec is not None and (dec.date if dec.status in ("set", "review", "manual") else None) == note["linked_date"]:
            return dec.as_dict()  # same day as before: facts and mentions are unchanged
        facts = meeting.extract(note["body"] or "", ref=ref) if (not lf or kind in FACT_KINDS) else None
        with db.tx() as c:
            c.execute("DELETE FROM date_mentions WHERE note_id = ?", (note_id,))
            for m in ment[:40]:
                c.execute("INSERT INTO date_mentions(note_id, date, date_end, kind, snippet) VALUES (?,?,?,?,?)",
                          (note_id, m["date"], m.get("end"), m["kind"], m["snippet"]))
            c.execute("UPDATE notes SET facts = ? WHERE id = ?", (json.dumps(facts) if facts else None, note_id))
        return dec.as_dict() if dec else None

    def redecide_all(self, ctx: ScanContext) -> int:
        n = 0
        for r in db.q("SELECT id FROM notes WHERE lib_path IS NOT NULL AND trashed_at IS NULL"):
            before = db.q1("SELECT linked_date, date_status FROM notes WHERE id = ?", (r["id"],))
            self.apply_dates(r["id"], ctx=ctx, light=True)
            after = db.q1("SELECT linked_date, date_status FROM notes WHERE id = ?", (r["id"],))
            if before != after:
                n += 1
                changed("notes", r["id"])
        return n

    def set_date(self, note_id: str, *, day: str | None = None, end: str | None = None, time_: str | None = None,
                 none: bool = False, clear: bool = False, by: str = "you", note: str | None = None) -> dict:
        """A decision from you, the local AI or Claude. Library files keep it in .daybook/dates.json."""
        row = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
        if not row:
            raise LibraryError("That note no longer exists.")
        for v in (day, end):
            if v:
                date.fromisoformat(v)
        if time_ and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", time_):
            raise ValueError("Use a 24-hour time like 14:30.")
        root = self.root() if row["lib_path"] else None
        if row["lib_path"] and root:
            entry = None if clear else ({"none": True, "by": by, "note": note} if none else
                                        {"date": day, "end": end, "time": time_, "by": by, "note": note})
            with self.lock:
                datescan.set_entry(root, row["lib_path"], entry)
                try:
                    self._sidecar_mtime = datescan.sidecar_path(root).stat().st_mtime_ns
                except OSError:
                    pass
            self.apply_dates(note_id)
        else:
            if clear:
                with db.tx() as c:
                    c.execute("UPDATE notes SET date_status = NULL, date_source = NULL WHERE id = ?", (note_id,))
                self.apply_dates(note_id)
            else:
                info = jloads(row["date_info"], {}) or {}
                info.update({"evidence": note or datescan.SOURCE_LABELS.get(by, "Set by hand"), "suggestion": None})
                with db.tx() as c:
                    c.execute(
                        """UPDATE notes SET linked_date = ?, date_end = ?, date_time = ?, date_source = ?, date_confidence = 1.0,
                               date_status = 'manual', date_info = ? WHERE id = ?""",
                        (None if none else day, None if none else end, None if none else time_, "none" if none else by,
                         json.dumps(info), note_id))
                self.apply_dates(note_id)
        changed("notes", note_id)
        return db.q1("SELECT id, linked_date, date_end, date_time, date_source, date_status, date_confidence FROM notes WHERE id = ?", (note_id,))

    def needs_review(self, limit: int = 200) -> list[dict]:
        return db.q(
            """SELECT id, title, lib_path, lib_kind, linked_date, date_status, date_confidence, date_source, date_info, notebook_id,
                      substr(body, 1, 600) AS head
               FROM notes WHERE trashed_at IS NULL AND date_status IN ('suggested', 'review', 'none')
                 AND (lib_path IS NOT NULL OR kind = 'import')
               ORDER BY CASE date_status WHEN 'review' THEN 0 WHEN 'suggested' THEN 1 ELSE 2 END, title LIMIT ?""",
            (limit,))

    # -- writing Markdown back ----------------------------------------------
    def write_back(self, note_id: str, *, renamed: bool = False) -> dict:
        """Save a library Markdown note to its file. Returns {'path', 'conflict'}."""
        with self.lock:
            note = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
            root = self.root()
            if not note or not note["lib_path"] or root is None:
                return {}
            rel = note["lib_path"]
            path = safe_rel(root, rel)
            lf = db.q1("SELECT * FROM lib_files WHERE path = ?", (rel,))
            current = path.read_bytes() if path.exists() else None
            if current is not None and lf and lf["synced_hash"] and _sha(current) != lf["synced_hash"]:
                # Changed on disk since Daybook last read it: keep both versions.
                conflict = _unique(path.with_name(f"{path.stem} (Daybook copy {datetime.now().strftime('%Y-%m-%d %H%M')}){path.suffix}"))
                raw_fm, _k, _b = split_front_matter(current.decode("utf-8", "replace"))
                _atomic_write(conflict, (raw_fm + (note["body"] or "")).encode("utf-8"))
                ctx = self._context(root)
                ctx.force = True
                self.index_file(root, rel, ctx)
                self.index_file(root, conflict.relative_to(root).as_posix(), ctx)
                changed("notes")
                return {"path": rel, "conflict": conflict.relative_to(root).as_posix()}
            raw_fm = ""
            if current is not None:
                raw_fm, _k, _b = split_front_matter(current.decode("utf-8", "replace"))
            data = (raw_fm + (note["body"] or "")).encode("utf-8")
            if renamed:
                new_path = _unique(path.with_name(safe_filename(note["title"] or "Untitled", "Untitled") + path.suffix)) \
                    if (safe_filename(note["title"] or "Untitled", "Untitled") + path.suffix) != path.name else path
                if new_path != path:
                    self._pin_date_before_rename(root, note)
                    if path.exists():
                        os.replace(path, new_path)
                    new_rel = new_path.relative_to(root).as_posix()
                    with db.tx() as c:
                        c.execute("UPDATE lib_files SET path = ? WHERE path = ?", (new_rel, rel))
                        c.execute("UPDATE notes SET lib_path = ?, source_name = ? WHERE id = ?", (new_rel, new_path.name, note_id))
                    datescan.rename_entry(root, rel, new_rel)
                    rel, path = new_rel, new_path
            try:
                if current is None or _sha(current) != _sha(data) or renamed:
                    _atomic_write(path, data)
                st = path.stat()
            except OSError as exc:
                msg = f"Couldn't save to {rel}: {exc.strerror or exc}"
                self._record_error(rel, msg)
                return {"path": rel, "conflict": None, "error": msg}
            with db.tx() as c:
                c.execute("""UPDATE lib_files SET size = ?, mtime_ns = ?, sha256 = ?, synced_hash = ?, status = 'ok', error = NULL,
                              indexed_at = ? WHERE path = ?""",
                          (st.st_size, st.st_mtime_ns, _sha(data), _sha(data), now_ms(), rel))
            return {"path": rel, "conflict": None}

    def _pin_date_before_rename(self, root: Path, note: dict) -> None:
        """A date that came from the old file name is written down so renaming doesn't lose it."""
        if note["date_source"] in ("filename", "title") and note["linked_date"]:
            datescan.set_entry(root, note["lib_path"], {"date": note["linked_date"], "end": note["date_end"],
                                                        "time": note["date_time"], "by": "you",
                                                        "note": "Kept from the file name when the note was renamed"})

    def materialize(self, note_id: str) -> str | None:
        """Give a new note in a library notebook its Markdown file."""
        with self.lock:
            note = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
            root = self.root()
            if not note or note["lib_path"] or note["locked"] or root is None:
                return None
            nb = db.q1("SELECT * FROM notebooks WHERE id = ?", (note["notebook_id"],)) if note["notebook_id"] else None
            if not nb or nb["lib_dir"] is None:
                return None
            if not (note["title"] or "").strip() and not (note["body"] or "").strip():
                return None
            folder = root / nb["lib_dir"] if nb["lib_dir"] else root
            name = safe_filename(note["title"] or f"Untitled {today_str()}", "Untitled") + ".md"
            path = _unique(folder / name)
            data = (note["body"] or "").encode("utf-8")
            _atomic_write(path, data)
            rel = path.relative_to(root).as_posix()
            st = path.stat()
            meta = {"cv": CONVERTER_VERSION, "props": {}, "kind": "markdown", "ext": ".md", "made_here": True}
            with db.tx() as c:
                c.execute(
                    """INSERT INTO lib_files(path, note_id, kind, size, mtime_ns, sha256, synced_hash, status, meta, first_seen, indexed_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (rel, note_id, "markdown", st.st_size, st.st_mtime_ns, _sha(data), _sha(data), "ok", json.dumps(meta), now_ms(), now_ms()))
                c.execute("UPDATE notes SET lib_path = ?, lib_kind = 'markdown', source_name = ? WHERE id = ?", (rel, path.name, note_id))
            return rel

    def move_note(self, note_id: str, target_nb: str | None) -> None:
        """Change a library note's notebook: moves its file between top-level folders."""
        with self.lock:
            note = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
            root = self.root()
            if not note or root is None:
                return
            target = db.q1("SELECT * FROM notebooks WHERE id = ?", (target_nb,)) if target_nb else None
            if note["lib_path"]:
                if not target or target["lib_dir"] is None:
                    if note["lib_kind"] != "markdown":
                        raise ValueError("Files from your library stay in its folders. Move the file in Finder instead.")
                    # Leave the library: the file goes to .daybook/trash; the note stays in Daybook.
                    self.trash_file(note_id)
                    with db.tx() as c:
                        c.execute("DELETE FROM lib_files WHERE note_id = ?", (note_id,))
                        c.execute("UPDATE notes SET lib_path = NULL, lib_kind = NULL, kind = 'note', notebook_id = ? WHERE id = ?",
                                  (target_nb, note_id))
                    return
                src = safe_rel(root, note["lib_path"])
                dest_dir = root / target["lib_dir"] if target["lib_dir"] else root
                dest = _unique(dest_dir / src.name)
                os.replace(src, dest)
                new_rel = dest.relative_to(root).as_posix()
                st = dest.stat()
                with db.tx() as c:
                    c.execute("UPDATE lib_files SET path = ?, mtime_ns = ?, size = ? WHERE path = ?",
                              (new_rel, st.st_mtime_ns, st.st_size, note["lib_path"]))
                    c.execute("UPDATE notes SET lib_path = ?, notebook_id = ? WHERE id = ?", (new_rel, target_nb, note_id))
                datescan.rename_entry(root, note["lib_path"], new_rel)
            elif target and target["lib_dir"] is not None:
                if note["locked"]:
                    raise ValueError("Locked notes stay private. Remove the lock first to move it into your library.")
                with db.tx() as c:
                    c.execute("UPDATE notes SET notebook_id = ? WHERE id = ?", (target_nb, note_id))
                self.materialize(note_id)

    def trash_file(self, note_id: str) -> str | None:
        """Move a library file into .daybook/trash (restorable)."""
        with self.lock:
            note = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
            root = self.root()
            if not note or not note["lib_path"] or root is None:
                return None
            src = safe_rel(root, note["lib_path"])
            if not src.exists():
                return None
            dest = root / datescan.SIDECAR_DIR / "trash" / datetime.now().strftime("%Y%m%d-%H%M%S") / note["lib_path"]
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
            trash_rel = dest.relative_to(root).as_posix()
            lf = db.q1("SELECT meta FROM lib_files WHERE path = ?", (note["lib_path"],))
            meta = jloads(lf["meta"], {}) if lf else {}
            meta["trash"] = trash_rel
            with db.tx() as c:
                c.execute("UPDATE lib_files SET status = 'trashed', meta = ? WHERE path = ?", (json.dumps(meta), note["lib_path"]))
            return trash_rel

    def restore_file(self, note_id: str) -> None:
        with self.lock:
            note = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
            root = self.root()
            if not note or not note["lib_path"] or root is None:
                return
            lf = db.q1("SELECT * FROM lib_files WHERE path = ?", (note["lib_path"],))
            meta = jloads(lf["meta"], {}) if lf else {}
            src = root / meta["trash"] if meta.get("trash") else None
            if not src or not src.exists():
                return
            dest = _unique(root / note["lib_path"])
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(src, dest)
            new_rel = dest.relative_to(root).as_posix()
            meta.pop("trash", None)
            st = dest.stat()
            with db.tx() as c:
                c.execute("UPDATE lib_files SET path = ?, status = 'ok', meta = ?, size = ?, mtime_ns = ? WHERE path = ?",
                          (new_rel, json.dumps(meta), st.st_size, st.st_mtime_ns, note["lib_path"]))
                c.execute("UPDATE notes SET lib_path = ? WHERE id = ?", (new_rel, note_id))

    def forget_file(self, note_id: str) -> None:
        """Delete forever: the trashed copy goes too."""
        with self.lock:
            note = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
            root = self.root()
            if not note or not note["lib_path"]:
                return
            lf = db.q1("SELECT * FROM lib_files WHERE path = ?", (note["lib_path"],))
            meta = jloads(lf["meta"], {}) if lf else {}
            if root and meta.get("trash"):
                try:
                    (root / meta["trash"]).unlink()
                except OSError:
                    pass
            with db.tx() as c:
                c.execute("DELETE FROM lib_files WHERE path = ?", (note["lib_path"],))

    # -- views ----------------------------------------------------------------
    def status(self) -> dict:
        root = self.root()
        s = db.get_settings()["library"]
        out = {**self.public_state(), "configured": root is not None, "root": str(root) if root else None,
               "enabled": bool(s.get("enabled", True)), "auto_root": str(auto_root()) if auto_root() else None,
               "path_setting": s.get("path") or "", "env": bool(os.environ.get("DAYBOOK_LIBRARY"))}
        if root:
            out["name"] = datescan.load_config(root).get("name") or root.name
            out["counts"] = {r["kind"]: r["n"] for r in db.q(
                "SELECT kind, count(*) AS n FROM lib_files WHERE status = 'ok' GROUP BY kind")}
            out["files"] = sum(out["counts"].values())
            out["errors"] = db.q("SELECT path, error FROM lib_files WHERE status = 'error' ORDER BY path LIMIT 50")
            out["review"] = db.scalar(
                "SELECT count(*) FROM notes WHERE trashed_at IS NULL AND lib_path IS NOT NULL AND date_status IN ('suggested','review','none')") or 0
            out["dated"] = db.scalar(
                "SELECT count(*) FROM notes WHERE trashed_at IS NULL AND lib_path IS NOT NULL AND linked_date IS NOT NULL") or 0
            out["sidecar"] = str(datescan.sidecar_path(root))
            out["decisions"] = len(datescan.load_sidecar(root)["dates"])
            out["folders"] = db.q(
                """SELECT b.id, b.name, b.lib_dir, b.color, (SELECT count(*) FROM notes n WHERE n.notebook_id = b.id AND n.trashed_at IS NULL) AS count
                   FROM notebooks b WHERE b.lib_dir IS NOT NULL ORDER BY b.lib_dir""")
        return out

    def git_status(self) -> dict:
        root = self.root()
        if not root or not (root / ".git").exists():
            return {"repo": False}
        try:
            out = subprocess.run(["git", "--no-optional-locks", "-c", "core.quotepath=off", "status", "--porcelain=v1", "-b"],
                                 cwd=str(root), capture_output=True, text=True, timeout=10)
            last = subprocess.run(["git", "--no-optional-locks", "log", "-1", "--format=%h%x09%aI%x09%s"],
                                  cwd=str(root), capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"repo": True, "error": str(exc)}
        lines = out.stdout.splitlines()
        head = lines[0][3:] if lines and lines[0].startswith("##") else ""
        m = re.match(r"([^.\s]+)(?:\.\.\.(\S+))?(?: \[(.*)\])?", head)
        ahead = behind = 0
        if m and m.group(3):
            a = re.search(r"ahead (\d+)", m.group(3))
            b = re.search(r"behind (\d+)", m.group(3))
            ahead, behind = int(a.group(1)) if a else 0, int(b.group(1)) if b else 0
        changes = []
        for l in lines[1:]:
            code, p = l[:2], l[3:].strip().strip('"')
            if " -> " in p:
                p = p.split(" -> ", 1)[1]
            changes.append({"code": code.strip() or "?", "path": p})
        parts = last.stdout.strip().split("\t")
        return {"repo": True, "branch": m.group(1) if m else None, "upstream": m.group(2) if m else None,
                "ahead": ahead, "behind": behind, "changes": changes[:200], "changed": len(changes),
                "last_commit": {"hash": parts[0], "when": parts[1], "message": parts[2]} if len(parts) == 3 else None}


engine = Library()


# ---------------------------------------------------------------------------
# Relations computed on the fly
# ---------------------------------------------------------------------------
_VERSION_TOKENS = re.compile(r"\b(?:v\d+(?:[._]\d+)*|version\s*\d+(?:\.\d+)*|draft(?:\s*\d+)?|final|rev\s*\d+|copy|\(\d+\))\b", re.I)


def family_key(title: str) -> str:
    t = _VERSION_TOKENS.sub(" ", title or "")
    t = re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    return t


def version_label(title: str) -> str | None:
    m = re.search(r"\bv(\d+(?:[._]\d+)*)\b", title or "", re.I)
    return f"v{m.group(1).replace('_', '.')}" if m else None


def related(note: dict) -> dict:
    """Versions / meetings in the same series, and recordings linked to meeting notes by time."""
    out: dict = {"family": [], "recordings": [], "notes_for_recording": []}
    if not note.get("lib_path"):
        return out
    key = family_key(note["title"])
    if key:
        rows = db.q("""SELECT id, title, linked_date, date_time, lib_kind, lib_path, updated_at FROM notes
                       WHERE lib_path IS NOT NULL AND trashed_at IS NULL AND id != ? AND lib_kind = ?""",
                    (note["id"], note.get("lib_kind")))
        fam = [r for r in rows if family_key(r["title"]) == key]
        for r in fam:
            r["version"] = version_label(r["title"])
        fam.sort(key=lambda r: (r["linked_date"] or "", r["version"] or "", r["title"]))
        out["family"] = fam
        out["family_kind"] = "versions" if (version_label(note["title"]) or any(r["version"] for r in fam)) else "series"
    day = note.get("linked_date")
    if day:
        if note.get("lib_kind") == "transcript":
            cands = db.q("""SELECT id, title, date_time, lib_path, lib_kind FROM notes WHERE linked_date = ? AND trashed_at IS NULL
                            AND lib_kind IN ('document', 'markdown', 'pdf') AND id != ?""", (day, note["id"]))
            out["notes_for_recording"] = _near(cands, note.get("date_time"))
        elif note.get("lib_kind") in ("document", "markdown", "pdf"):
            cands = db.q("""SELECT id, title, date_time, lib_path, lib_kind FROM notes WHERE linked_date = ? AND trashed_at IS NULL
                            AND lib_kind = 'transcript' AND id != ?""", (day, note["id"]))
            out["recordings"] = _near(cands, note.get("date_time"))
    return out


def _near(cands: list[dict], hhmm: str | None, minutes: int = 25) -> list[dict]:
    if not hhmm:
        return []

    def mins(s):
        h, m = s.split(":")
        return int(h) * 60 + int(m)
    out = []
    for c in cands:
        if c.get("date_time") and abs(mins(c["date_time"]) - mins(hhmm)) <= minutes:
            out.append(c)
    return out


def note_lib_info(note: dict) -> dict | None:
    if not note.get("lib_path"):
        return None
    lf = db.q1("SELECT size, status, error, meta, kind FROM lib_files WHERE path = ?", (note["lib_path"],))
    meta = jloads(lf["meta"], {}) if lf else {}
    root = engine.root()
    return {
        "path": note["lib_path"],
        "folder": folder_of(note["lib_path"]),
        "filename": Path(note["lib_path"]).name,
        "kind": note.get("lib_kind"),
        "kind_label": KIND_LABELS.get(note.get("lib_kind") or "", "File"),
        "editable": note.get("lib_kind") in EDITABLE,
        "url": file_url(note["lib_path"]),
        "size": lf["size"] if lf else None,
        "status": lf["status"] if lf else "unknown",
        "error": lf["error"] if lf else None,
        "doc_title": meta.get("doc_title"),
        "slides": meta.get("slides"),
        "library": (datescan.load_config(root).get("name") or root.name) if root else None,
        "local": sys.platform == "darwin",
    }
