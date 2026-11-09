"""Reading documents inside Daybook (the Reader).

A note that came from a file (a deck, a PDF, a spreadsheet or a picture, from
the library folder or from an import) can be read in its original form: slides
drawn from the .pptx with their speaker notes, PDF pages, spreadsheet tabs.
Each page's text is indexed so a search can land on the right slide or page,
and passages can be highlighted and commented on. Highlights can be gathered
into a note of their own.

Everything is computed and stored on this computer. Drawn slides are cached in
the data folder's cache/ (derived from the files, safe to delete at any time).
Locked notes can't be read here, and locking a note removes its page text and
highlights.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path

from .. import db
from ..bus import changed
from ..util import new_id, now_ms
from . import attachments, converters, notes, slides

log = logging.getLogger("daybook")

INDEX_VERSION = 1
MODES = {"deck": "slides", "pdf": "pdf", "sheet": "sheet", "image": "image"}
INDEXED = ("deck", "pdf", "sheet")
COLORS = ("yellow", "green", "blue", "pink", "purple")
MEDIA_NAME = re.compile(r"^m\d{1,5}\.(png|jpg|gif|webp|bmp)$")
MEDIA_TOKEN = "__DBMEDIA__/"
MAX_PDF_PAGES = 600
MAX_SHEET_ROWS_INDEXED = 3000
MAX_COLS = 120

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class NotReadable(Exception):
    """The note has nothing the Reader can show in this mode."""


@dataclass
class Source:
    path: Path
    name: str
    kind: str | None
    sha: str
    size: int


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def kind_for_name(name: str) -> str | None:
    ext = Path(name or "").suffix.lower()
    if ext in converters.SLIDES:
        return "deck"
    if ext in converters.PDF:
        return "pdf"
    if ext in converters.SHEETS or ext in converters.DELIMITED:
        return "sheet"
    if ext in converters.IMAGES and ext not in (".heic", ".heif", ".tif", ".tiff"):
        return "image"
    return None


def source(row: dict) -> Source | None:
    """The original file behind a note: its library file, or the file it was imported from."""
    if row.get("lib_path"):
        from . import library

        root = library.engine.root()
        if not root:
            return None
        try:
            path = library.safe_rel(root, row["lib_path"])
        except PermissionError:
            return None
        if not path.is_file():
            return None
        lf = db.q1("SELECT sha256, size FROM lib_files WHERE path = ?", (row["lib_path"],))
        sha = (lf or {}).get("sha256") or _file_sha(path)
        return Source(path, path.name, kind_for_name(path.name), sha, path.stat().st_size)
    att = db.q1(
        "SELECT * FROM attachments WHERE note_id = ? AND role = 'original' ORDER BY created_at DESC LIMIT 1",
        (row["id"],),
    )
    if not att:
        return None
    try:
        path = attachments.path_of(att)
    except PermissionError:
        return None
    if not path.is_file():
        return None
    return Source(path, att["filename"], kind_for_name(att["filename"]), att.get("sha256") or _file_sha(path),
                  att.get("size") or path.stat().st_size)


def _readable_row(note_id: str) -> dict:
    row = notes.get_row(note_id)
    if row["locked"]:
        raise notes.Locked(note_id)
    return row


def mode_for(row: dict, src: Source | None) -> str:
    if src and src.kind in MODES:
        return MODES[src.kind]
    if row.get("lib_kind") == "mockup":
        return "mockup"
    return "doc"


def manifest(note_id: str) -> dict:
    row = _readable_row(note_id)
    src = source(row)
    mode = mode_for(row, src)
    idx = db.q1("SELECT pages, kind FROM doc_index WHERE note_id = ?", (note_id,))
    count = db.q1("SELECT COUNT(*) AS n FROM annotations WHERE note_id = ?", (note_id,))["n"]
    return {
        "id": note_id,
        "title": row["title"] or (src.name if src else "Untitled"),
        "mode": mode,
        "filename": src.name if src else None,
        "size": src.size if src else None,
        "raw": f"/api/reader/{note_id}/raw" if src else None,
        "lib_path": row.get("lib_path"),
        "lib_kind": row.get("lib_kind"),
        "notebook_id": row.get("notebook_id"),
        "linked_date": row.get("linked_date"),
        "updated_at": row["updated_at"],
        "pages": idx["pages"] if idx else None,
        "annotations": count,
        "highlights_note": _highlights_note_id(note_id),
    }


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------
def _cache_dir(sha: str) -> Path:
    return db.get_paths().cache / "reader" / f"{sha[:32]}-v{slides.RENDERER_VERSION}"


def _deck_cached(src: Source) -> dict:
    d = _cache_dir(src.sha)
    f = d / "deck.json"
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            try:
                f.touch()  # recently used; housekeeping keeps it
            except OSError:
                pass
            return data
        except (OSError, ValueError):
            pass
    with _lock_for(src.sha):
        if f.exists():
            try:
                return json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass
        t0 = time.monotonic()
        rendered = slides.render(src.path, lambda name: MEDIA_TOKEN + name)
        tmp = d.with_name(d.name + f".tmp-{new_id()[:6]}")
        tmp.mkdir(parents=True, exist_ok=True)
        for name, blob in rendered.media.items():
            (tmp / name).write_bytes(blob)
        data = {"width": rendered.width, "height": rendered.height, "title": rendered.title,
                "warnings": rendered.warnings, "slides": rendered.slides, "version": slides.RENDERER_VERSION}
        (tmp / "deck.json").write_text(json.dumps(data), encoding="utf-8")
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        tmp.rename(d)
        log.info("Drew %s slides of %s in %.2fs", len(rendered.slides), src.name, time.monotonic() - t0)
        return data


def deck(note_id: str) -> dict:
    row = _readable_row(note_id)
    src = source(row)
    if not src or src.kind != "deck":
        raise NotReadable("This note doesn't have a slide deck to show.")
    try:
        data = _deck_cached(src)
    except slides.DeckError as exc:
        raise NotReadable(str(exc)) from exc
    base = f"/api/reader/{note_id}/media/"
    out = dict(data)
    out["slides"] = [{**s, "html": s["html"].replace(MEDIA_TOKEN, base)} for s in data["slides"]]
    return out


def media(note_id: str, name: str) -> Path:
    if not MEDIA_NAME.match(name or ""):
        raise NotReadable("No such picture.")
    row = _readable_row(note_id)
    src = source(row)
    if not src or src.kind != "deck":
        raise NotReadable("No such picture.")
    p = _cache_dir(src.sha) / name
    if not p.exists():
        _deck_cached(src)
    if not p.exists():
        raise NotReadable("No such picture.")
    return p


def raw(note_id: str) -> Source:
    row = _readable_row(note_id)
    src = source(row)
    if not src:
        raise NotReadable("This note has no original file.")
    return src


# ---------------------------------------------------------------------------
# Spreadsheets
# ---------------------------------------------------------------------------
def _cell_text(v, fmt: str | None = None) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M") if (v.hour or v.minute) else v.strftime("%Y-%m-%d")
    if isinstance(v, (date, dtime)):
        return v.isoformat()
    if isinstance(v, (int, float)):
        try:
            return slides.format_number(float(v), fmt) if fmt else slides.format_number(float(v), "General")
        except Exception:
            return str(v)
    return str(v)


def _trim(rows: list[list[str]]) -> list[list[str]]:
    while rows and not any(c.strip() for c in rows[-1]):
        rows.pop()
    width = 0
    for r in rows:
        for i in range(len(r) - 1, -1, -1):
            if r[i].strip():
                width = max(width, i + 1)
                break
    return [r[:width] + [""] * (width - len(r[:width])) for r in rows]


def _read_delimited(path: Path) -> list[list[str]]:
    raw_bytes = path.read_bytes()[: 30 * 1024 * 1024]
    text = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw_bytes.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    text = text or ""
    delim = "\t" if path.suffix.lower() == ".tsv" else None
    if delim is None:
        try:
            delim = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|").delimiter
        except csv.Error:
            delim = ","
    return [[c for c in row[:MAX_COLS]] for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _sheet_rows(src: Source, name: str | None, offset: int, limit: int) -> dict:
    ext = src.path.suffix.lower()
    if ext in converters.DELIMITED:
        rows = _trim(_read_delimited(src.path))
        return {"sheets": [{"name": src.name}], "name": src.name, "rows": rows[offset:offset + limit],
                "offset": offset, "total": len(rows)}
    from openpyxl import load_workbook

    wb = load_workbook(src.path, read_only=True, data_only=True)
    try:
        names = wb.sheetnames
        current = name if name in names else names[0]
        ws = wb[current]
        rows: list[list[str]] = []
        total = 0
        for r in ws.iter_rows(max_col=MAX_COLS):
            total += 1
            if total <= offset or len(rows) >= limit:
                if total > offset + limit + 50000:
                    break
                continue
            rows.append([_cell_text(getattr(c, "value", None), getattr(c, "number_format", None)) for c in r])
        rows = _trim(rows)
        return {"sheets": [{"name": n} for n in names], "name": current, "rows": rows, "offset": offset, "total": total}
    finally:
        wb.close()


def sheet(note_id: str, name: str | None = None, offset: int = 0, limit: int = 500) -> dict:
    row = _readable_row(note_id)
    src = source(row)
    if not src or src.kind != "sheet":
        raise NotReadable("This note doesn't have a spreadsheet to show.")
    offset = max(0, int(offset))
    limit = max(1, min(2000, int(limit)))
    try:
        return _sheet_rows(src, name, offset, limit)
    except NotReadable:
        raise
    except Exception as exc:
        raise NotReadable("This spreadsheet couldn't be opened. It may be damaged or password-protected.") from exc


# ---------------------------------------------------------------------------
# Page-level text (search lands on the right slide or page)
# ---------------------------------------------------------------------------
def _page_texts(src: Source) -> list[tuple[int, str, str]]:
    if src.kind == "deck":
        data = _deck_cached(src)
        out = []
        for s in data["slides"]:
            text = s.get("text") or ""
            if s.get("notes"):
                text += "\n\nSpeaker notes: " + s["notes"]
            out.append((s["n"], f"Slide {s['n']}: {s.get('title') or ''}".strip().rstrip(":"), text))
        return out
    if src.kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(str(src.path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return []
        out = []
        for i, page in enumerate(reader.pages[:MAX_PDF_PAGES], 1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            out.append((i, f"Page {i}", re.sub(r"[ \t]+\n", "\n", text)))
        return out
    if src.kind == "sheet":
        out = []
        first = _sheet_rows(src, None, 0, MAX_SHEET_ROWS_INDEXED)
        names = [s["name"] for s in first["sheets"]]
        for i, nm in enumerate(names[:30], 1):
            data = first if nm == first["name"] else _sheet_rows(src, nm, 0, MAX_SHEET_ROWS_INDEXED)
            text = "\n".join(" | ".join(c for c in r if c) for r in data["rows"])
            out.append((i, f"Sheet: {nm}", text))
        return out
    return []


def index_note(note_id: str) -> int | None:
    row = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
    if not row or row["locked"] or row["trashed_at"]:
        return None
    src = source(row)
    if not src or src.kind not in INDEXED:
        with db.tx() as c:
            c.execute("DELETE FROM doc_pages WHERE note_id = ?", (note_id,))
            c.execute("DELETE FROM doc_index WHERE note_id = ?", (note_id,))
        return None
    err = None
    try:
        pages = _page_texts(src)
    except Exception as exc:  # a damaged file shouldn't stop the others
        log.info("Couldn't read pages of %s: %s", src.name, exc)
        pages, err = [], str(exc)[:300]
    with db.tx() as c:
        c.execute("DELETE FROM doc_pages WHERE note_id = ?", (note_id,))
        c.executemany(
            "INSERT INTO doc_pages(note_id, page, label, text) VALUES (?,?,?,?)",
            [(note_id, p, label[:300], text[:40000]) for p, label, text in pages],
        )
        c.execute(
            """INSERT INTO doc_index(note_id, sha256, kind, pages, version, error, indexed_at) VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(note_id) DO UPDATE SET sha256 = excluded.sha256, kind = excluded.kind, pages = excluded.pages,
                 version = excluded.version, error = excluded.error, indexed_at = excluded.indexed_at""",
            (note_id, src.sha, src.kind, len(pages), INDEX_VERSION, err, now_ms()),
        )
    return len(pages)


def pending(limit: int = 20) -> list[str]:
    """Notes whose pages need (re)indexing: new files, changed files, or an older index."""
    out: list[str] = []
    lib = db.q(
        """SELECT n.id FROM notes n JOIN lib_files f ON f.note_id = n.id
           LEFT JOIN doc_index d ON d.note_id = n.id
           WHERE n.trashed_at IS NULL AND n.locked = 0 AND n.lib_kind IN ('deck', 'pdf', 'sheet')
             AND (d.note_id IS NULL OR d.sha256 != f.sha256 OR d.version < ?)
           ORDER BY n.updated_at DESC LIMIT ?""",
        (INDEX_VERSION, limit),
    )
    out += [r["id"] for r in lib]
    if len(out) < limit:
        imp = db.q(
            """SELECT n.id, a.filename FROM notes n JOIN attachments a ON a.note_id = n.id AND a.role = 'original'
               LEFT JOIN doc_index d ON d.note_id = n.id
               WHERE n.trashed_at IS NULL AND n.locked = 0 AND n.lib_path IS NULL
                 AND (d.note_id IS NULL OR d.sha256 != COALESCE(a.sha256, '') OR d.version < ?)
               ORDER BY n.updated_at DESC LIMIT ?""",
            (INDEX_VERSION, limit * 4),
        )
        for r in imp:
            if kind_for_name(r["filename"]) in INDEXED and r["id"] not in out:
                out.append(r["id"])
            if len(out) >= limit:
                break
    return out


def index_pending(limit: int = 10) -> int:
    done = 0
    for nid in pending(limit):
        try:
            if index_note(nid) is not None:
                done += 1
        except Exception:
            log.exception("Page index failed for %s", nid)
    if done:
        changed("reader")
    return done


def search_pages(query: str, limit: int = 8) -> list[dict]:
    text, where, _params, _chips = notes.parse_search(query or "")
    if where:
        return []  # filtered searches are about notes; page hits would ignore the filters
    match = notes.fts_match(text)
    if not match:
        return []
    try:
        rows = db.q(
            """SELECT p.note_id, p.page, p.label, n.title, n.lib_kind,
                      snippet(doc_pages_fts, 1, char(2), char(3), '…', 14) AS snip
               FROM doc_pages_fts JOIN doc_pages p ON p.id = doc_pages_fts.rowid
               JOIN notes n ON n.id = p.note_id
               WHERE doc_pages_fts MATCH ? AND n.trashed_at IS NULL AND n.locked = 0
               ORDER BY bm25(doc_pages_fts) LIMIT ?""",
            (match, limit),
        )
    except Exception:
        return []
    return [{"note_id": r["note_id"], "page": r["page"], "label": r["label"], "title": r["title"],
             "kind": r["lib_kind"], "snippet_html": notes._mark(r["snip"] or "")} for r in rows]


def find(note_id: str, q: str, limit: int = 200) -> list[dict]:
    """Pages of one document that contain `q` (case-insensitive), with a short excerpt."""
    row = _readable_row(note_id)
    q = (q or "").strip()
    if not q:
        return []
    if not db.q1("SELECT 1 FROM doc_index WHERE note_id = ?", (note_id,)):
        src = source(row)
        if src and src.kind in INDEXED:
            index_note(note_id)
    needle = q.casefold()
    out = []
    for r in db.q("SELECT page, label, text FROM doc_pages WHERE note_id = ? ORDER BY page", (note_id,)):
        text = r["text"] or ""
        low = text.casefold()
        n = low.count(needle)
        if not n:
            continue
        i = low.find(needle)
        start = max(0, i - 50)
        out.append({"page": r["page"], "label": r["label"], "count": n,
                    "excerpt": ("…" if start else "") + text[start:i + len(q) + 60].replace("\n", " ")})
        if len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------------------
# Highlights and comments
# ---------------------------------------------------------------------------
def _ann_api(r: dict) -> dict:
    return {k: r[k] for k in ("id", "note_id", "page", "kind", "quote", "prefix", "suffix", "comment", "color",
                              "created_at", "updated_at")}


def list_annotations(note_id: str) -> list[dict]:
    _readable_row(note_id)
    rows = db.q("SELECT * FROM annotations WHERE note_id = ? ORDER BY COALESCE(page, 0), created_at", (note_id,))
    return [_ann_api(r) for r in rows]


def _clean(data: dict, *, partial: bool = False) -> dict:
    out: dict = {}
    if "page" in data or not partial:
        p = data.get("page")
        out["page"] = int(p) if p not in (None, "") else None
        if out["page"] is not None and not (0 < out["page"] < 100000):
            raise ValueError("That page number isn't valid.")
    for key, cap in (("quote", 4000), ("prefix", 120), ("suffix", 120), ("comment", 8000)):
        if key in data or not partial:
            out[key] = str(data.get(key) or "")[:cap]
    if "color" in data or not partial:
        c = data.get("color") or "yellow"
        out["color"] = c if c in COLORS else "yellow"
    if "kind" in data or not partial:
        k = data.get("kind") or "highlight"
        out["kind"] = k if k in ("highlight", "note") else "highlight"
    return out


def add_annotation(note_id: str, data: dict) -> dict:
    _readable_row(note_id)
    clean = _clean(data)
    if not clean["quote"].strip() and not clean["comment"].strip():
        raise ValueError("Select some text to highlight, or write a comment.")
    aid = new_id()
    ts = now_ms()
    with db.tx() as c:
        c.execute(
            """INSERT INTO annotations(id, note_id, page, kind, quote, prefix, suffix, comment, color, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, note_id, clean["page"], clean["kind"], clean["quote"], clean["prefix"], clean["suffix"],
             clean["comment"], clean["color"], ts, ts),
        )
    changed("annotations", note_id)
    return _ann_api(db.q1("SELECT * FROM annotations WHERE id = ?", (aid,)))


def update_annotation(aid: str, data: dict) -> dict:
    row = db.q1("SELECT * FROM annotations WHERE id = ?", (aid,))
    if not row:
        raise notes.NotFound(aid)
    _readable_row(row["note_id"])
    clean = {k: v for k, v in _clean(data, partial=True).items() if k in ("comment", "color")}
    if clean:
        sets = ", ".join(f"{k} = ?" for k in clean)
        with db.tx() as c:
            c.execute(f"UPDATE annotations SET {sets}, updated_at = ? WHERE id = ?", (*clean.values(), now_ms(), aid))
        changed("annotations", row["note_id"])
    return _ann_api(db.q1("SELECT * FROM annotations WHERE id = ?", (aid,)))


def delete_annotation(aid: str) -> None:
    row = db.q1("SELECT note_id FROM annotations WHERE id = ?", (aid,))
    if not row:
        return
    with db.tx() as c:
        c.execute("DELETE FROM annotations WHERE id = ?", (aid,))
    changed("annotations", row["note_id"])


def all_annotations(q: str | None = None, limit: int = 200) -> list[dict]:
    args: list = []
    where = "n.trashed_at IS NULL AND n.locked = 0"
    if q and q.strip():
        where += " AND (a.quote LIKE ? OR a.comment LIKE ? OR n.title LIKE ?)"
        like = f"%{q.strip()}%"
        args += [like, like, like]
    rows = db.q(
        f"""SELECT a.*, n.title AS note_title, n.lib_kind AS note_kind, n.lib_path AS note_path
            FROM annotations a JOIN notes n ON n.id = a.note_id
            WHERE {where} ORDER BY a.updated_at DESC LIMIT ?""",
        (*args, max(1, min(1000, limit))),
    )
    return [{**_ann_api(r), "note_title": r["note_title"], "note_kind": r["note_kind"], "note_path": r["note_path"]}
            for r in rows]


def _highlights_note_id(source_id: str) -> str | None:
    r = db.q1("SELECT h.note_id FROM reading_notes h JOIN notes n ON n.id = h.note_id "
              "WHERE h.source_id = ? AND n.trashed_at IS NULL", (source_id,))
    return r["note_id"] if r else None


def _page_word(mode: str) -> str:
    return {"slides": "Slide", "pdf": "Page", "sheet": "Sheet"}.get(mode, "Page")


def highlights_markdown(note_id: str) -> tuple[str, str]:
    row = _readable_row(note_id)
    src = source(row)
    mode = mode_for(row, src)
    title = row["title"] or (src.name if src else "Untitled")
    anns = list_annotations(note_id)
    labels = {r["page"]: r["label"] for r in db.q("SELECT page, label FROM doc_pages WHERE note_id = ?", (note_id,))}
    word = _page_word(mode)
    lines = [f"_Highlights and comments from **{title}**, gathered by Daybook's reader. "
             f"Saving again from the reader updates this note._", ""]
    last_page = object()
    for a in anns:
        if a["page"] != last_page:
            last_page = a["page"]
            if a["page"] is not None:
                label = labels.get(a["page"]) or f"{word} {a['page']}"
                if not label.lower().startswith(word.lower()):
                    label = f"{word} {a['page']}: {label}"
                lines += [f"## {label}", ""]
            else:
                lines += ["## Highlights", ""]
        if a["quote"].strip():
            lines += [f"> {line}" if line.strip() else ">" for line in a["quote"].strip().splitlines()]
            lines.append("")
        if a["comment"].strip():
            lines += [a["comment"].strip(), ""]
        link = f"#/read/{note_id}" + (f"?p={a['page']}" if a["page"] else "")
        lines += [f"[Open in the reader]({link})", ""]
    if not anns:
        lines.append("No highlights yet.")
    return f"Highlights — {title}"[:200], "\n".join(lines).strip() + "\n"


def save_highlights_note(note_id: str) -> dict:
    row = _readable_row(note_id)
    title, body = highlights_markdown(note_id)
    existing = _highlights_note_id(note_id)
    if existing:
        try:
            return notes.update(existing, {"title": title, "body": body}, force=True)
        except (notes.NotFound, notes.Locked, ValueError):
            pass
    created = notes.create(title=title, body=body, notebook_id=row.get("notebook_id"),
                           linked_date=row.get("linked_date"))
    with db.tx() as c:
        c.execute("INSERT INTO reading_notes(source_id, note_id) VALUES (?, ?) "
                  "ON CONFLICT(source_id) DO UPDATE SET note_id = excluded.note_id", (note_id, created["id"]))
    return created


# ---------------------------------------------------------------------------
# Housekeeping
# ---------------------------------------------------------------------------
def prune_cache(max_age_days: int = 45, max_bytes: int = 800 * 1024 * 1024) -> int:
    """Drop drawn decks that haven't been opened in a while, or the oldest when the cache gets big."""
    base = db.get_paths().cache / "reader"
    if not base.exists():
        return 0
    entries = []
    for d in base.iterdir():
        if not d.is_dir():
            continue
        if ".tmp-" in d.name:
            shutil.rmtree(d, ignore_errors=True)
            continue
        try:
            size = sum(p.stat().st_size for p in d.iterdir() if p.is_file())
            used = (d / "deck.json").stat().st_mtime if (d / "deck.json").exists() else d.stat().st_mtime
        except OSError:
            continue
        entries.append((used, size, d))
    removed = 0
    cutoff = time.time() - max_age_days * 86400
    total = sum(e[1] for e in entries)
    for used, size, d in sorted(entries):
        if used < cutoff or total > max_bytes or not d.name.endswith(f"-v{slides.RENDERER_VERSION}"):
            shutil.rmtree(d, ignore_errors=True)
            total -= size
            removed += 1
    return removed
