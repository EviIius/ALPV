"""Notes: CRUD, search, revisions, links, locking, daily notes, templates."""
from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import date, timedelta

from .. import db, security
from ..bus import changed
from ..util import (
    clean_tags,
    jloads,
    md_to_text,
    new_id,
    note_links,
    now_ms,
    snippet,
    today_str,
    word_count,
)

REVISION_INTERVAL_MS = 10 * 60 * 1000
MAX_REVISIONS = 60
TRASH_DAYS = 30


class Conflict(Exception):
    def __init__(self, note: dict):
        super().__init__("This note was changed on another device.")
        self.note = note


class NotFound(Exception):
    pass


class Locked(Exception):
    pass


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def to_api(row: dict, *, body: bool = True) -> dict:
    out = {
        "id": row["id"],
        "title": row["title"],
        "notebook_id": row["notebook_id"],
        "tags": jloads(row["tags"], []),
        "color": row["color"],
        "pinned": bool(row["pinned"]),
        "archived": bool(row["archived"]),
        "trashed_at": row["trashed_at"],
        "linked_date": row["linked_date"],
        "date_end": row.get("date_end"),
        "date_time": row.get("date_time"),
        "date_source": row.get("date_source"),
        "date_confidence": row.get("date_confidence"),
        "date_status": row.get("date_status"),
        "lib_path": row.get("lib_path"),
        "lib_kind": row.get("lib_kind"),
        "editable": not (row.get("lib_path") and row.get("lib_kind") != "markdown"),
        "kind": row["kind"],
        "source_name": row["source_name"],
        "locked": bool(row["locked"]),
        "rev": row["rev"],
        "word_count": row["word_count"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "has_suggestions": bool(row.get("ai_suggestions")),
    }
    if body:
        out["body"] = "" if row["locked"] else row["body"]
        out["summary"] = row["summary"]
        out["ai_suggestions"] = jloads(row.get("ai_suggestions"), None)
        out["date_info"] = jloads(row.get("date_info"), None)
        out["facts"] = jloads(row.get("facts"), None)
    else:
        out["snippet"] = "" if row["locked"] else snippet(row["body"])
    return out


def content_hash(title: str, body: str) -> str:
    return hashlib.sha256(f"{title}\n{body}".encode("utf-8")).hexdigest()[:24]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def get_row(note_id: str) -> dict:
    row = db.q1("SELECT * FROM notes WHERE id = ?", (note_id,))
    if not row:
        raise NotFound(note_id)
    return row


def get(note_id: str) -> dict:
    row = get_row(note_id)
    note = to_api(row)
    note["backlinks"] = backlinks(note_id)
    note["attachments"] = db.q(
        "SELECT id, filename, mime, size, role, created_at FROM attachments WHERE note_id = ? ORDER BY created_at",
        (note_id,),
    )
    note["events"] = db.q("SELECT id, title, start, all_day FROM events WHERE note_id = ? ORDER BY start", (note_id,))
    note["tasks"] = db.q(
        "SELECT id, title, done, due_date FROM tasks WHERE note_id = ? ORDER BY done, due_date", (note_id,)
    )
    note["mentions"] = db.q(
        "SELECT date, date_end, kind, snippet FROM date_mentions WHERE note_id = ? ORDER BY date LIMIT 60", (note_id,)
    )
    if row.get("lib_path"):
        from . import library

        note["lib"] = library.note_lib_info(row)
        note["related"] = library.related(row)
    else:
        nb = db.q1("SELECT lib_dir FROM notebooks WHERE id = ?", (row["notebook_id"],)) if row["notebook_id"] else None
        note["lib_pending"] = bool(nb and nb["lib_dir"] is not None)
    return note


def create(
    *,
    title: str = "",
    body: str = "",
    notebook_id: str | None = None,
    tags=None,
    linked_date: str | None = None,
    color: str | None = None,
    kind: str = "note",
    source_name: str | None = None,
    summary: str | None = None,
    ai_suggestions: dict | None = None,
    pinned: bool = False,
    created_at: int | None = None,
    updated_at: int | None = None,
    note_id: str | None = None,
    emit: bool = True,
    skip_dating: bool = False,
) -> dict:
    ts = now_ms()
    nid = note_id or new_id()
    title = (title or "").strip()[:300]
    body = body or ""
    if notebook_id and not db.q1("SELECT id FROM notebooks WHERE id = ?", (notebook_id,)):
        notebook_id = None
    date_status = date_source = None
    if linked_date:
        date_status, date_source = ("set", "daily") if kind == "daily" else ("manual", "manual")
    with db.tx() as c:
        c.execute(
            """INSERT INTO notes(id, title, body, notebook_id, tags, color, pinned, linked_date, kind, source_name,
                   summary, ai_suggestions, word_count, created_at, updated_at, date_status, date_source, date_confidence)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                nid,
                title,
                body,
                notebook_id,
                json.dumps(clean_tags(tags)),
                color,
                1 if pinned else 0,
                linked_date,
                kind,
                source_name,
                summary,
                json.dumps(ai_suggestions) if ai_suggestions else None,
                word_count(body),
                created_at or ts,
                updated_at or ts,
                date_status,
                date_source,
                1.0 if linked_date else None,
            ),
        )
        _write_links(c, nid, body)
    if not skip_dating and kind != "daily":
        from . import library

        try:
            library.engine.materialize(nid)
            library.engine.apply_dates(nid)
        except Exception:  # dating must never block saving a note
            import logging

            logging.getLogger("daybook").exception("Couldn't date note %s", nid)
    if emit:
        changed("notes", nid)
    return get(nid)


UPDATABLE = {"title", "body", "notebook_id", "tags", "color", "pinned", "archived", "linked_date", "summary", "kind"}


def update(note_id: str, patch: dict, *, base_rev: int | None = None, force: bool = False) -> dict:
    from . import library

    row = get_row(note_id)
    if row["locked"] and ("body" in patch):
        raise Locked("Unlock this note to edit it.")
    if base_rev is not None and not force and int(base_rev) != row["rev"]:
        raise Conflict(get(note_id))
    patch = dict(patch)
    lib_doc = bool(row.get("lib_path")) and row.get("lib_kind") != "markdown"
    if lib_doc and (("body" in patch and patch["body"] != row["body"]) or ("title" in patch and patch["title"] != row["title"])):
        raise ValueError("This file is read-only in Daybook. Open it to edit the original, or make an editable copy.")
    date_patch = {k: patch.pop(k) for k in ("linked_date", "date_end", "date_time") if k in patch}
    if "notebook_id" in patch and patch["notebook_id"] != row["notebook_id"]:
        target = patch["notebook_id"] or None
        tnb = db.q1("SELECT lib_dir FROM notebooks WHERE id = ?", (target,)) if target else None
        if row.get("lib_path") or (tnb and tnb["lib_dir"] is not None):
            library.engine.move_note(note_id, target)
            patch.pop("notebook_id")
            changed("notes", note_id)
    if date_patch:
        day = date_patch.get("linked_date", row["linked_date"])
        if day:
            library.engine.set_date(note_id, day=day, end=date_patch.get("date_end"), time_=date_patch.get("date_time"), by="you")
        else:
            library.engine.set_date(note_id, none=True, by="you")
    fields: dict = {}
    for k, v in patch.items():
        if k not in UPDATABLE:
            continue
        if k == "tags":
            v = json.dumps(clean_tags(v))
        elif k in ("pinned", "archived"):
            v = 1 if v else 0
        elif k == "title":
            v = (v or "").strip()[:300]
        elif k == "notebook_id" and v and not db.q1("SELECT id FROM notebooks WHERE id = ?", (v,)):
            v = None
        fields[k] = v
    if not fields:
        return get(note_id)
    content_changed = ("body" in fields and fields["body"] != row["body"]) or (
        "title" in fields and fields["title"] != row["title"]
    )
    ts = now_ms()
    with db.tx() as c:
        if content_changed and not row["locked"]:
            _maybe_snapshot(c, row, fields)
        if "body" in fields:
            fields["word_count"] = word_count(fields["body"])
        fields["updated_at"] = ts
        sets = ", ".join(f"{k} = ?" for k in fields)
        c.execute(f"UPDATE notes SET {sets}, rev = rev + 1 WHERE id = ?", (*fields.values(), note_id))
        if "body" in fields:
            _write_links(c, note_id, fields["body"])
    lib_note = None
    if content_changed and not row["locked"]:
        if row.get("lib_path") and row.get("lib_kind") == "markdown":
            lib_note = library.engine.write_back(note_id, renamed="title" in fields and fields["title"] != row["title"])
        elif not row.get("lib_path"):
            library.engine.materialize(note_id)
        try:
            library.engine.apply_dates(note_id)
        except Exception:
            import logging

            logging.getLogger("daybook").exception("Couldn't date note %s", note_id)
    changed("notes", note_id)
    out = get(note_id)
    if lib_note and lib_note.get("conflict"):
        out["lib_conflict"] = lib_note["conflict"]
    return out


def _maybe_snapshot(c, row: dict, fields: dict) -> None:
    last = c.execute(
        "SELECT created_at, body FROM note_revisions WHERE note_id = ? ORDER BY created_at DESC LIMIT 1",
        (row["id"],),
    ).fetchone()
    old_body = row["body"] or ""
    new_body = fields.get("body", old_body) or ""
    big_change = abs(len(new_body) - len(old_body)) > max(400, 0.3 * len(old_body))
    if not old_body.strip() and not row["title"]:
        return
    if last is None or (now_ms() - last[0]) > REVISION_INTERVAL_MS or big_change:
        if last is not None and last[1] == old_body:
            return
        c.execute(
            "INSERT INTO note_revisions(note_id, title, body, created_at) VALUES (?,?,?,?)",
            (row["id"], row["title"], old_body, now_ms()),
        )
        c.execute(
            """DELETE FROM note_revisions WHERE note_id = ? AND id NOT IN
                 (SELECT id FROM note_revisions WHERE note_id = ? ORDER BY created_at DESC LIMIT ?)""",
            (row["id"], row["id"], MAX_REVISIONS),
        )


def _write_links(c, note_id: str, body: str) -> None:
    c.execute("DELETE FROM note_links WHERE src_id = ?", (note_id,))
    for dst in note_links(body):
        if dst != note_id:
            c.execute("INSERT OR IGNORE INTO note_links(src_id, dst_id) VALUES (?, ?)", (note_id, dst))


def backlinks(note_id: str) -> list[dict]:
    return db.q(
        """SELECT n.id, n.title, n.updated_at FROM note_links l JOIN notes n ON n.id = l.src_id
           WHERE l.dst_id = ? AND n.trashed_at IS NULL ORDER BY n.updated_at DESC""",
        (note_id,),
    )


def trash(note_id: str) -> None:
    row = get_row(note_id)
    if row.get("lib_path"):
        from . import library

        library.engine.trash_file(note_id)
    with db.tx() as c:
        c.execute("UPDATE notes SET trashed_at = ?, pinned = 0, updated_at = ? WHERE id = ?", (now_ms(), now_ms(), note_id))
    changed("notes", note_id)


def restore(note_id: str) -> dict:
    row = get_row(note_id)
    if row.get("lib_path"):
        from . import library

        library.engine.restore_file(note_id)
        lf = db.q1("SELECT status FROM lib_files WHERE path = (SELECT lib_path FROM notes WHERE id = ?)", (note_id,))
        if lf and lf["status"] == "missing" and row.get("lib_kind") == "markdown":
            library.engine.write_back(note_id)  # deleted outside Daybook: write it back from the saved copy
    with db.tx() as c:
        c.execute("UPDATE notes SET trashed_at = NULL, updated_at = ? WHERE id = ?", (now_ms(), note_id))
    changed("notes", note_id)
    return get(note_id)


def delete_forever(note_id: str) -> None:
    from . import attachments

    row = get_row(note_id)
    if row.get("lib_path"):
        from . import library

        library.engine.forget_file(note_id)
    for a in db.q("SELECT id FROM attachments WHERE note_id = ?", (note_id,)):
        attachments.delete(a["id"])
    with db.tx() as c:
        c.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        c.execute("DELETE FROM note_links WHERE dst_id = ?", (note_id,))
    changed("notes", note_id)


def empty_trash(older_than_days: int | None = None) -> int:
    if older_than_days is None:
        rows = db.q("SELECT id FROM notes WHERE trashed_at IS NOT NULL")
    else:
        cutoff = now_ms() - older_than_days * 86400 * 1000
        rows = db.q("SELECT id FROM notes WHERE trashed_at IS NOT NULL AND trashed_at < ?", (cutoff,))
    for r in rows:
        delete_forever(r["id"])
    return len(rows)


def duplicate(note_id: str) -> dict:
    row = get_row(note_id)
    if row["locked"]:
        raise Locked("Locked notes can't be duplicated.")
    return create(
        title=f"{row['title']} (copy)".strip(),
        body=row["body"],
        notebook_id=row["notebook_id"],
        tags=jloads(row["tags"], []),
        color=row["color"],
        linked_date=row["linked_date"],
    )


# ---------------------------------------------------------------------------
# Listing & search
# ---------------------------------------------------------------------------
def fts_query(text: str) -> str | None:
    terms = re.findall(r"[\w][\w'’.\-]*", text or "", re.UNICODE)
    terms = [t.strip(".-'’") for t in terms if t.strip(".-'’")]
    if not terms:
        return None
    quoted = ['"' + t.replace('"', '""') + '"' for t in terms]
    quoted[-1] = quoted[-1] + "*"
    return " ".join(quoted)


# Search operators: in:Kelly type:deck before:2026-10-20 after:2026-10 on:2026-10-23 person:Jacqueline
# tag:sas #sas is:pinned has:actions -word "exact phrase". Everything else is ordinary search text.
OPERATOR = re.compile(r'(?<!\S)(-?)(in|type|kind|tag|before|after|on|date|is|has|person|with|folder|from):("[^"]*"|\S+)', re.I)
TYPE_ALIASES = {
    "deck": "deck", "decks": "deck", "pptx": "deck", "powerpoint": "deck", "slides": "deck", "ppt": "deck",
    "pdf": "pdf", "pdfs": "pdf", "doc": "document", "docs": "document", "docx": "document", "word": "document",
    "document": "document", "sheet": "sheet", "sheets": "sheet", "xlsx": "sheet", "excel": "sheet", "csv": "sheet",
    "mockup": "mockup", "mockups": "mockup", "html": "mockup", "email": "email", "mail": "email", "eml": "email",
    "transcript": "transcript", "recording": "transcript", "recordings": "transcript", "image": "image",
    "picture": "image", "text": "text", "txt": "text", "code": "code", "md": "markdown", "markdown": "markdown",
}


def _date_bound(value: str, end: bool) -> str | None:
    v = value.strip().lower()
    today = date.today()
    if v == "today":
        return today.isoformat()
    if v == "yesterday":
        return (today - timedelta(days=1)).isoformat()
    if v == "tomorrow":
        return (today + timedelta(days=1)).isoformat()
    m = re.fullmatch(r"-?(\d+)([dwm])", v)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        days = n * {"d": 1, "w": 7, "m": 30}[unit]
        return (today - timedelta(days=days)).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return v
    if re.fullmatch(r"\d{4}-\d{2}", v):
        y, mo = int(v[:4]), int(v[5:7])
        if not end:
            return f"{v}-01"
        nxt = date(y + (mo == 12), (mo % 12) + 1, 1)
        return (nxt - timedelta(days=1)).isoformat()
    if re.fullmatch(r"\d{4}", v):
        return f"{v}-12-31" if end else f"{v}-01-01"
    return None


def parse_search(text: str) -> tuple[str, list[str], list, list[dict]]:
    """Split a search into plain text plus SQL filters. Returns (text, where, params, chips)."""
    where: list[str] = []
    params: list = []
    chips: list[dict] = []
    rest = text or ""
    for m in list(OPERATOR.finditer(text or "")):
        neg, key, raw = m.group(1) == "-", m.group(2).lower(), m.group(3).strip('"')
        clause, args = None, []
        if key in ("in", "folder", "from"):
            clause = ("(n.notebook_id IN (SELECT id FROM notebooks WHERE lower(name) LIKE ? OR lower(COALESCE(lib_dir, '')) LIKE ?)"
                      " OR lower(COALESCE(n.lib_path, '')) LIKE ?)")
            args = [f"{raw.lower()}%", f"{raw.lower()}%", f"{raw.lower()}/%"]
        elif key in ("type", "kind"):
            k = TYPE_ALIASES.get(raw.lower())
            if raw.lower() in ("note", "notes"):
                clause = "(n.lib_kind IS NULL OR n.lib_kind = 'markdown')"
            elif raw.lower() in ("daily",):
                clause = "n.kind = 'daily'"
            elif raw.lower() in ("import", "imported"):
                clause = "n.kind = 'import'"
            elif k:
                clause, args = "n.lib_kind = ?", [k]
                if k in ("deck", "pdf", "sheet", "document", "image"):
                    ext = {"deck": ".pptx", "pdf": ".pdf", "sheet": ".xlsx", "document": ".docx", "image": ".png"}[k]
                    clause = f"({clause} OR (n.kind = 'import' AND lower(COALESCE(n.source_name, '')) LIKE ?))"
                    args.append(f"%{ext}")
        elif key == "tag":
            clause, args = "EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value = ?)", [raw.lower().lstrip("#")]
        elif key in ("before", "after", "on", "date"):
            if key == "before":
                d = _date_bound(raw, end=False)
                clause, args = ("n.linked_date < ?", [d]) if d else (None, [])
            elif key == "after":
                d = _date_bound(raw, end=True) if re.fullmatch(r"\d{4}(-\d{2})?", raw) else _date_bound(raw, end=False)
                clause, args = ("n.linked_date > ?" if re.fullmatch(r"\d{4}(-\d{2})?", raw) else "n.linked_date >= ?", [d]) if d else (None, [])
            else:
                lo, hi = _date_bound(raw, end=False), _date_bound(raw, end=True)
                clause, args = ("(n.linked_date BETWEEN ? AND ? OR (n.linked_date < ? AND n.date_end >= ?))", [lo, hi, lo, lo]) if lo else (None, [])
        elif key == "is":
            clause = {"pinned": "n.pinned = 1", "archived": "n.archived = 1", "locked": "n.locked = 1",
                      "undated": "n.linked_date IS NULL", "dated": "n.linked_date IS NOT NULL",
                      "library": "n.lib_path IS NOT NULL", "private": "n.lib_path IS NULL",
                      "daily": "n.kind = 'daily'", "editable": "(n.lib_path IS NULL OR n.lib_kind IN ('markdown', 'text'))",
                      "highlighted": "EXISTS (SELECT 1 FROM annotations a WHERE a.note_id = n.id)"}.get(raw.lower())
        elif key == "has":
            clause = {"actions": "json_array_length(COALESCE(json_extract(n.facts, '$.actions'), '[]')) > 0",
                      "decisions": "json_array_length(COALESCE(json_extract(n.facts, '$.decisions'), '[]')) > 0",
                      "questions": "json_array_length(COALESCE(json_extract(n.facts, '$.questions'), '[]')) > 0",
                      "highlights": "EXISTS (SELECT 1 FROM annotations a WHERE a.note_id = n.id)",
                      "tasks": "EXISTS (SELECT 1 FROM tasks t WHERE t.note_id = n.id AND t.done = 0)",
                      "events": "EXISTS (SELECT 1 FROM events e WHERE e.note_id = n.id)",
                      "attachments": "EXISTS (SELECT 1 FROM attachments x WHERE x.note_id = n.id)",
                      "people": "json_array_length(COALESCE(json_extract(n.facts, '$.people'), '[]')) > 0"}.get(raw.lower())
        elif key in ("person", "with"):
            clause, args = "EXISTS (SELECT 1 FROM json_each(n.facts, '$.people') p WHERE lower(p.value) LIKE ?)", [f"{raw.lower()}%"]
        if clause is None:
            continue
        where.append(f"NOT {clause}" if neg else clause)
        params += args
        chips.append({"key": key, "value": raw, "negated": neg})
        rest = rest.replace(m.group(0), " ", 1)
    # #tag shorthand
    for m in list(re.finditer(r"(?<!\S)#([\w-]{2,})", rest)):
        where.append("EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value = ?)")
        params.append(m.group(1).lower())
        chips.append({"key": "tag", "value": m.group(1), "negated": False})
        rest = rest.replace(m.group(0), " ", 1)
    return " ".join(rest.split()), where, params, chips


def fts_match(text: str) -> str | None:
    """Plain search text to an FTS5 query: words (last one as a prefix), "exact phrases" and -excluded words."""
    phrases = re.findall(r'"([^"]+)"', text or "")
    rest = re.sub(r'"[^"]*"', " ", text or "")
    excluded = [w[1:] for w in rest.split() if w.startswith("-") and len(w) > 1]
    rest = " ".join(w for w in rest.split() if not (w.startswith("-") and len(w) > 1))
    base = fts_query(rest) if rest.strip() else None
    parts = [base] if base else []
    parts += ['"' + p.replace('"', '""') + '"' for p in phrases if p.strip()]
    if not parts:
        return None
    q = " ".join(parts)
    for w in excluded:
        t = re.sub(r"[^\w'’.\-]", "", w)
        if t:
            q += ' NOT "' + t.replace('"', '""') + '"'
    return q


def _mark(snip: str) -> str:
    return html.escape(snip).replace("\x02", "<mark>").replace("\x03", "</mark>")


def list_notes(
    *,
    view: str = "all",
    notebook_id: str | None = None,
    tag: str | None = None,
    query: str | None = None,
    sort: str = "updated",
    linked_date: str | None = None,
    lib_kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> dict:
    where = []
    params: list = []
    if view == "trash":
        where.append("n.trashed_at IS NOT NULL")
    else:
        where.append("n.trashed_at IS NULL")
        if view == "archived":
            where.append("n.archived = 1")
        elif view == "pinned":
            where.append("n.pinned = 1 AND n.archived = 0")
        elif view == "daily":
            where.append("n.kind = 'daily'")
        elif view == "imported":
            where.append("n.kind = 'import'")
        elif view == "locked":
            where.append("n.locked = 1")
        elif view == "library":
            where.append("n.lib_path IS NOT NULL")
        elif view == "review":
            where.append("n.date_status IN ('suggested', 'review', 'none') AND (n.lib_path IS NOT NULL OR n.kind = 'import')")
        elif view == "dated":
            where.append("n.linked_date IS NOT NULL")
        else:
            where.append("n.archived = 0")
    if notebook_id == "none":
        where.append("n.notebook_id IS NULL")
    elif notebook_id:
        where.append("n.notebook_id = ?")
        params.append(notebook_id)
    if tag:
        where.append("EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value = ?)")
        params.append(tag.lower())
    if linked_date:
        where.append("(n.linked_date = ? OR (n.linked_date < ? AND n.date_end >= ?))")
        params += [linked_date, linked_date, linked_date]
    if lib_kind:
        where.append("n.lib_kind = ?")
        params.append(lib_kind)

    match = None
    if query:
        text, extra_where, extra_params, _chips = parse_search(query)
        where += extra_where
        params += extra_params
        match = fts_match(text) if text else None
    order = {
        "updated": "n.pinned DESC, n.updated_at DESC",
        "created": "n.pinned DESC, n.created_at DESC",
        "title": "n.pinned DESC, lower(n.title) ASC",
        "date": "n.linked_date IS NULL, n.linked_date DESC, n.date_time DESC, n.updated_at DESC",
    }.get(sort, "n.pinned DESC, n.updated_at DESC")
    if view == "trash":
        order = "n.trashed_at DESC"

    if match:
        sql = f"""SELECT n.*, snippet(notes_fts, 1, char(2), char(3), '…', 14) AS snip, bm25(notes_fts, 6.0, 1.0, 3.0) AS score
                  FROM notes_fts JOIN notes n ON n.seq = notes_fts.rowid
                  WHERE notes_fts MATCH ? AND {' AND '.join(where)}
                  ORDER BY score LIMIT ? OFFSET ?"""
        rows = db.q(sql, (match, *params, limit, offset))
        total = len(rows)
    else:
        sql = f"SELECT n.* FROM notes n WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?"
        rows = db.q(sql, (*params, limit, offset))
        total = db.scalar(f"SELECT count(*) FROM notes n WHERE {' AND '.join(where)}", tuple(params))
    items = []
    for r in rows:
        item = to_api(r, body=False)
        if match and r.get("snip") and not r["locked"]:
            item["snippet_html"] = _mark(r["snip"])
        items.append(item)
    return {"items": items, "total": total}


def search(query: str, limit: int = 8) -> list[dict]:
    return list_notes(query=query, limit=limit)["items"]


def find_by_title(title: str) -> dict | None:
    return db.q1(
        "SELECT * FROM notes WHERE trashed_at IS NULL AND lower(title) = lower(?) ORDER BY updated_at DESC LIMIT 1",
        (title.strip(),),
    )


def all_tags() -> list[dict]:
    return db.q(
        """SELECT j.value AS tag, count(*) AS count FROM notes n, json_each(n.tags) j
           WHERE n.trashed_at IS NULL GROUP BY j.value ORDER BY count DESC, tag ASC"""
    )


def counts() -> dict:
    row = db.q1(
        """SELECT
             sum(CASE WHEN trashed_at IS NULL AND archived = 0 THEN 1 ELSE 0 END) AS all_notes,
             sum(CASE WHEN trashed_at IS NULL AND pinned = 1 AND archived = 0 THEN 1 ELSE 0 END) AS pinned,
             sum(CASE WHEN trashed_at IS NULL AND archived = 1 THEN 1 ELSE 0 END) AS archived,
             sum(CASE WHEN trashed_at IS NOT NULL THEN 1 ELSE 0 END) AS trash,
             sum(CASE WHEN trashed_at IS NULL AND kind = 'daily' THEN 1 ELSE 0 END) AS daily,
             sum(CASE WHEN trashed_at IS NULL AND kind = 'import' THEN 1 ELSE 0 END) AS imported,
             sum(CASE WHEN trashed_at IS NULL AND locked = 1 THEN 1 ELSE 0 END) AS locked,
             sum(CASE WHEN trashed_at IS NULL AND lib_path IS NOT NULL THEN 1 ELSE 0 END) AS library,
             sum(CASE WHEN trashed_at IS NULL AND date_status IN ('suggested', 'review', 'none')
                      AND (lib_path IS NOT NULL OR kind = 'import') THEN 1 ELSE 0 END) AS review
           FROM notes"""
    )
    return {k: (v or 0) for k, v in (row or {}).items()}


# ---------------------------------------------------------------------------
# Revisions
# ---------------------------------------------------------------------------
def revisions(note_id: str) -> list[dict]:
    get_row(note_id)
    rows = db.q(
        "SELECT id, title, body, created_at FROM note_revisions WHERE note_id = ? ORDER BY created_at DESC",
        (note_id,),
    )
    return [
        {"id": r["id"], "title": r["title"], "created_at": r["created_at"], "words": word_count(r["body"]),
         "preview": snippet(r["body"], 140)}
        for r in rows
    ]


def revision(note_id: str, rev_id: int) -> dict:
    r = db.q1("SELECT * FROM note_revisions WHERE note_id = ? AND id = ?", (note_id, rev_id))
    if not r:
        raise NotFound(str(rev_id))
    return r


def restore_revision(note_id: str, rev_id: int) -> dict:
    r = revision(note_id, rev_id)
    row = get_row(note_id)
    # Snapshot the current version first so a restore can itself be undone.
    with db.tx() as c:
        c.execute(
            "INSERT INTO note_revisions(note_id, title, body, created_at) VALUES (?,?,?,?)",
            (note_id, row["title"], row["body"], now_ms()),
        )
    return update(note_id, {"title": r["title"], "body": r["body"]}, force=True)


# ---------------------------------------------------------------------------
# Locked notes (AES-256-GCM, one vault passphrase)
# ---------------------------------------------------------------------------
def lock(note_id: str, passphrase: str) -> dict:
    row = get_row(note_id)
    if row["locked"]:
        return get(note_id)
    if row.get("lib_path"):
        raise ValueError("This note is a file in your library, so it can't be encrypted in place. "
                         "Move it to a private notebook first, then lock it.")
    if not security.vault_is_set():
        err = security.validate_new_passphrase(passphrase)
        if err:
            raise ValueError(err)
        security.set_vault(passphrase)
    elif not security.check_vault(passphrase):
        raise PermissionError("That isn't your vault passphrase.")
    cipher = security.encrypt_text(row["body"] or "", passphrase)
    with db.tx() as c:
        c.execute(
            """UPDATE notes SET locked = 1, cipher = ?, body = '', summary = NULL, ai_suggestions = NULL,
                   word_count = 0, rev = rev + 1, updated_at = ? WHERE id = ?""",
            (cipher, now_ms(), note_id),
        )
        # Plain-text copies must not survive locking.
        c.execute("DELETE FROM note_revisions WHERE note_id = ?", (note_id,))
        c.execute("DELETE FROM chunks WHERE note_id = ?", (note_id,))
        c.execute("DELETE FROM embed_state WHERE note_id = ?", (note_id,))
        c.execute("DELETE FROM note_links WHERE src_id = ?", (note_id,))
        c.execute("DELETE FROM doc_pages WHERE note_id = ?", (note_id,))
        c.execute("DELETE FROM doc_index WHERE note_id = ?", (note_id,))
        c.execute("DELETE FROM annotations WHERE note_id = ?", (note_id,))
        # Definitions found in this note leave the glossary until it's unlocked (the next scan re-reads the rest).
        c.execute("DELETE FROM glossary WHERE source_note = ? AND origin = 'found'", (note_id,))
        c.execute("UPDATE glossary SET source_note = NULL WHERE source_note = ?", (note_id,))
    from . import glossary

    glossary.invalidate()
    changed("notes", note_id)
    return get(note_id)


def unlock_view(note_id: str, passphrase: str) -> dict:
    row = get_row(note_id)
    if not row["locked"]:
        return get(note_id)
    if not security.check_vault(passphrase):
        raise PermissionError("That isn't your vault passphrase.")
    note = get(note_id)
    note["body"] = security.decrypt_text(row["cipher"], passphrase)
    note["unlocked"] = True
    return note


def save_locked(note_id: str, passphrase: str, title: str | None, body: str, base_rev: int | None = None) -> dict:
    row = get_row(note_id)
    if not row["locked"]:
        raise ValueError("This note isn't locked.")
    if not security.check_vault(passphrase):
        raise PermissionError("That isn't your vault passphrase.")
    if base_rev is not None and int(base_rev) != row["rev"]:
        raise Conflict(get(note_id))
    with db.tx() as c:
        c.execute(
            "UPDATE notes SET cipher = ?, title = ?, rev = rev + 1, updated_at = ? WHERE id = ?",
            (security.encrypt_text(body or "", passphrase), (title if title is not None else row["title"])[:300], now_ms(), note_id),
        )
    changed("notes", note_id)
    note = get(note_id)
    note["body"] = body
    note["unlocked"] = True
    return note


def remove_lock(note_id: str, passphrase: str) -> dict:
    row = get_row(note_id)
    if not row["locked"]:
        return get(note_id)
    if not security.check_vault(passphrase):
        raise PermissionError("That isn't your vault passphrase.")
    body = security.decrypt_text(row["cipher"], passphrase)
    with db.tx() as c:
        c.execute(
            "UPDATE notes SET locked = 0, cipher = NULL, body = ?, word_count = ?, rev = rev + 1, updated_at = ? WHERE id = ?",
            (body, word_count(body), now_ms(), note_id),
        )
        _write_links(c, note_id, body)
    changed("notes", note_id)
    return get(note_id)


def change_vault(old: str, new: str) -> int:
    if not security.check_vault(old):
        raise PermissionError("The current vault passphrase is wrong.")
    err = security.validate_new_passphrase(new)
    if err:
        raise ValueError(err)
    rows = db.q("SELECT id, cipher FROM notes WHERE locked = 1")
    with db.tx() as c:
        for r in rows:
            plain = security.decrypt_text(r["cipher"], old)
            c.execute("UPDATE notes SET cipher = ? WHERE id = ?", (security.encrypt_text(plain, new), r["id"]))
    security.set_vault(new)
    return len(rows)


# ---------------------------------------------------------------------------
# Daily notes & templates
# ---------------------------------------------------------------------------
def _long_date(d: date) -> str:
    return f"{d.strftime('%A')}, {d.strftime('%B')} {d.day}, {d.year}"


def daily(day: str | None = None, create_if_missing: bool = True) -> dict | None:
    day = day or today_str()
    d = date.fromisoformat(day)
    row = db.q1(
        "SELECT id FROM notes WHERE kind = 'daily' AND linked_date = ? AND trashed_at IS NULL ORDER BY created_at LIMIT 1",
        (day,),
    )
    if row:
        return get(row["id"])
    if not create_if_missing:
        return None
    from . import events as events_svc

    agenda = events_svc.occurrences_between(d, d)
    lines = []
    if agenda:
        lines.append("## Schedule")
        for ev in agenda:
            when = "All day" if ev["all_day"] else _fmt_time(ev["start"][11:16])
            lines.append(f"- {when}: {ev['title']}")
        lines.append("")
    lines += ["## Focus", "- [ ] ", "", "## Notes", "", ""]
    nb = db.q1("SELECT id FROM notebooks WHERE lower(name) = 'journal'")
    return create(
        title=_long_date(d),
        body="\n".join(lines),
        kind="daily",
        linked_date=day,
        tags=["daily"],
        notebook_id=nb["id"] if nb else None,
    )


def _fmt_time(hhmm: str) -> str:
    try:
        h, m = (int(x) for x in hhmm.split(":"))
    except ValueError:
        return hhmm
    suffix = "AM" if h < 12 else "PM"
    return f"{(h % 12) or 12}:{m:02d} {suffix}"


BUILTIN_TEMPLATES = [
    {
        "id": "meeting",
        "name": "Meeting notes",
        "title": "Meeting: ",
        "body": "## Details\n- **Date:** {{date}}\n- **Attendees:** \n\n## Agenda\n- \n\n## Notes\n\n\n## Decisions\n- \n\n## Action items\n- [ ] \n",
    },
    {
        "id": "one-on-one",
        "name": "One-on-one",
        "title": "1:1 — {{date}}",
        "body": "## Wins since last time\n- \n\n## Topics\n- \n\n## Feedback\n\n\n## Action items\n- [ ] \n",
    },
    {
        "id": "project",
        "name": "Project brief",
        "title": "Project: ",
        "body": "## Problem\n\n\n## Goal\n\n\n## Scope\n- **In:** \n- **Out:** \n\n## Stakeholders\n- \n\n## Milestones\n| Milestone | Date | Owner |\n| --- | --- | --- |\n|  |  |  |\n\n## Risks\n- \n",
    },
    {
        "id": "incident",
        "name": "Incident review",
        "title": "Incident: ",
        "body": "## Summary\n\n\n## Impact\n- **Systems:** \n- **Data affected:** \n- **Window:** \n\n## Timeline\n| Time | What happened |\n| --- | --- |\n|  |  |\n\n## Root cause\n\n\n## Fix\n\n\n## Follow-ups\n- [ ] \n",
    },
    {
        "id": "decision",
        "name": "Decision record",
        "title": "Decision: ",
        "body": "## Context\n\n\n## Options considered\n1. \n2. \n\n## Decision\n\n\n## Consequences\n- \n",
    },
    {
        "id": "weekly",
        "name": "Weekly review",
        "title": "Week of {{date}}",
        "body": "## What went well\n- \n\n## What got stuck\n- \n\n## Next week's priorities\n- [ ] \n",
    },
]


def templates() -> list[dict]:
    out = [dict(t, builtin=True) for t in BUILTIN_TEMPLATES]
    for r in db.q(
        """SELECT n.id, n.title, n.body FROM notes n WHERE n.trashed_at IS NULL AND n.locked = 0
           AND EXISTS (SELECT 1 FROM json_each(n.tags) WHERE json_each.value = 'template') ORDER BY n.title"""
    ):
        out.append({"id": f"note:{r['id']}", "name": r["title"] or "Untitled template", "title": r["title"], "body": r["body"], "builtin": False})
    return out


def render_template(template_id: str) -> tuple[str, str]:
    tpl = next((t for t in templates() if t["id"] == template_id), None)
    if not tpl:
        raise NotFound(template_id)
    d = date.fromisoformat(today_str())
    ctx = {"date": _long_date(d), "iso_date": d.isoformat()}

    def fill(s: str) -> str:
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: ctx.get(m.group(1), m.group(0)), s or "")

    return fill(tpl["title"]), fill(tpl["body"])


def plain_text(note_id: str) -> str:
    row = get_row(note_id)
    return md_to_text(row["body"])
