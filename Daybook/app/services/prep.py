"""Meeting prep: everything you already know about an upcoming meeting, on one page.

For a calendar event it gathers, from your own notes and library: who's involved,
what happened last time, open follow-ups (especially the attendees'), decisions
already made, questions still open, the decks and documents to have open, and
related notes and tasks. Rule-based and instant; the local AI can turn it into a
short brief on request. Nothing leaves this computer.
"""
from __future__ import annotations

import re
from datetime import date

from .. import db
from ..util import jloads, md_to_text
from . import events, notes

STOP = set("""a an and or the of to in on for with at by from into about over under after before
meeting meetings call calls sync syncs session sessions review reviews weekly daily monthly biweekly quarterly
notes note status update updates check checkin check-in catch catchup catch-up chat discussion discuss touch
base kickoff kick-off follow followup follow-up working work team teams w re fw fwd prep 1on1 1:1 one on
part day days week next new draft final v1 v2 v3 tbd am pm""".split())


def _words(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9&'-]{1,}", text or ""):
        lw = w.lower().strip("'-")
        if len(lw) >= 3 and lw not in STOP:
            out.append(lw)
    return out


def _norm_title(t: str) -> str:
    t = re.sub(r"\b(19|20)\d{2}[-_/]\d{1,2}[-_/]\d{1,2}\b", " ", t or "")
    t = re.sub(r"\b(notes?|draft|v\d+(\.\d+)?|final)\b", " ", t, flags=re.I)
    return " ".join(_words(t))


def _similar(a: str, b: str) -> float:
    wa, wb = set(_words(a)), set(_words(b))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def known_people() -> dict[str, int]:
    counts: dict[str, int] = {}
    for r in db.q("SELECT facts FROM notes WHERE trashed_at IS NULL AND locked = 0 AND facts IS NOT NULL"):
        for p in (jloads(r["facts"], {}) or {}).get("people") or []:
            if p and len(p) >= 2:
                counts[p] = counts.get(p, 0) + 1
    return counts


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", text or "", re.I) is not None


def build(event_id: str, start: str | None = None) -> dict:
    ev = events.get(event_id)
    when = start or ev["start"]
    day = when[:10]
    text = " ".join([ev.get("title") or "", ev.get("description") or "", ev.get("location") or ""])
    people = [p for p, _n in sorted(known_people().items(), key=lambda kv: -kv[1]) if _mentions(text, p)][:12]
    key_words = list(dict.fromkeys(_words(ev.get("title") or "")))[:8]
    title_norm = _norm_title(ev.get("title") or "")

    # Candidate notes: the linked note, notes from earlier events in the same series, and notes that match by words.
    cand: dict[str, float] = {}
    if ev.get("note_id"):
        cand[ev["note_id"]] = 100.0
    for other in db.q("SELECT id, title, note_id, start FROM events WHERE note_id IS NOT NULL AND id != ?", (event_id,)):
        if _similar(_norm_title(other["title"]), title_norm) >= 0.6:
            cand[other["note_id"]] = max(cand.get(other["note_id"], 0), 30.0)
    if key_words:
        match = " OR ".join(f'"{w}"' for w in key_words)
        try:
            rows = db.q("""SELECT n.id, bm25(notes_fts, 6.0, 1.0, 3.0) AS score FROM notes_fts JOIN notes n ON n.rowid = notes_fts.rowid
                           WHERE notes_fts MATCH ? AND n.trashed_at IS NULL AND n.locked = 0 AND n.archived = 0
                           ORDER BY score LIMIT 40""", (match,))
        except Exception:
            rows = []
        for i, r in enumerate(rows):
            cand[r["id"]] = max(cand.get(r["id"], 0), 12.0 - i * 0.25)
    if people:
        like = [f"%{p}%" for p in people]
        rows = db.q("SELECT id FROM notes WHERE trashed_at IS NULL AND locked = 0 AND facts IS NOT NULL AND ("
                    + " OR ".join("facts LIKE ?" for _ in like) + ") ORDER BY linked_date DESC LIMIT 40", like)
        for r in rows:
            cand[r["id"]] = cand.get(r["id"], 0) + 4.0
    if not cand:
        return {"event": _event_brief(ev, when), "people": people, "last": None, "notes": [], "actions": [],
                "decisions": [], "questions": [], "documents": [], "tasks": [], "keywords": key_words}
    ids = list(cand)
    rows = db.q(f"""SELECT id, title, linked_date, date_time, lib_kind, lib_path, facts, summary, body, kind, updated_at
                   FROM notes WHERE id IN ({','.join('?' * len(ids))})""", ids)
    scored = []
    for r in rows:
        s = cand[r["id"]]
        s += 8 * _similar(_norm_title(r["title"]), title_norm)
        f = jloads(r["facts"], {}) or {}
        s += 2 * len({p.lower() for p in people} & {p.lower() for p in (f.get("people") or [])})
        if r["linked_date"]:
            if r["linked_date"] > day:
                s -= 6  # notes about later dates matter less for prep
            else:
                try:
                    age = (date.fromisoformat(day) - date.fromisoformat(r["linked_date"])).days
                    s += max(0.0, 3 - age / 30)
                except ValueError:
                    pass
        scored.append((s, r))
    scored.sort(key=lambda x: -x[0])
    top = [r for s, r in scored if s >= 6][:10] or [r for _s, r in scored[:4]]

    # Last time: the most recent meeting-like note before this event.
    last = None
    for r in sorted(top, key=lambda r: (r["linked_date"] or "", r["date_time"] or ""), reverse=True):
        f = jloads(r["facts"], {}) or {}
        if r["linked_date"] and r["linked_date"] <= day and r["lib_kind"] not in ("deck", "sheet", "mockup") and (f or r["kind"] != "daily"):
            last = {"id": r["id"], "title": r["title"], "date": r["linked_date"],
                    "summary": (r["summary"] or md_to_text(r["body"] or "")[:420]).strip()}
            break

    actions, decisions, questions, documents = [], [], [], []
    tracked = {(t["note_id"], t["title"].strip().lower()) for t in db.q("SELECT note_id, title FROM tasks WHERE note_id IS NOT NULL")}
    people_l = [p.lower() for p in people]
    for r in top:
        f = jloads(r["facts"], {}) or {}
        for i, a in enumerate(f.get("actions") or []):
            if a.get("done"):
                continue
            owner = (a.get("owner") or "")
            mine = any(p in owner.lower() for p in people_l)
            actions.append({**a, "index": i, "note_id": r["id"], "title": r["title"], "date": r["linked_date"],
                            "attendee": mine, "tracked": (r["id"], a["text"].strip().lower()) in tracked})
        for d in f.get("decisions") or []:
            decisions.append({"text": d, "note_id": r["id"], "title": r["title"], "date": r["linked_date"]})
        for q in f.get("questions") or []:
            questions.append({"text": q, "note_id": r["id"], "title": r["title"], "date": r["linked_date"]})
        if r["lib_kind"] in ("deck", "pdf", "sheet", "document", "mockup") and (r["lib_kind"] != "document" or not f):
            documents.append({"id": r["id"], "title": r["title"], "kind": r["lib_kind"], "date": r["linked_date"]})
    actions.sort(key=lambda a: (not a["attendee"], a.get("due") or "9999", -(int((a.get("date") or "0").replace("-", "") or 0))))
    decisions.sort(key=lambda d: d.get("date") or "", reverse=True)
    questions.sort(key=lambda d: d.get("date") or "", reverse=True)
    # Open tasks tied to these notes.
    tids = [r["id"] for r in top]
    tasks_rows = db.q(f"""SELECT id, title, due_date, priority, note_id FROM tasks WHERE done = 0 AND note_id IN ({','.join('?' * len(tids))})
                         ORDER BY COALESCE(due_date, '9999'), priority DESC LIMIT 12""", tids) if tids else []
    related = [{"id": r["id"], "title": r["title"], "date": r["linked_date"], "kind": r["lib_kind"] or r["kind"]}
               for r in top if not last or r["id"] != last["id"]][:8]
    return {
        "event": _event_brief(ev, when),
        "people": people,
        "keywords": key_words,
        "last": last,
        "notes": related,
        "actions": actions[:14],
        "decisions": _dedupe(decisions)[:8],
        "questions": _dedupe(questions)[:6],
        "documents": documents[:6],
        "tasks": tasks_rows,
    }


def _dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        k = re.sub(r"\W+", " ", it["text"].lower()).strip()[:120]
        if k in seen:
            continue
        seen.add(k)
        out.append(it)
    return out


def _event_brief(ev: dict, when: str) -> dict:
    return {"id": ev["id"], "title": ev["title"], "start": when, "all_day": bool(ev.get("all_day")),
            "location": ev.get("location") or "", "note_id": ev.get("note_id"), "description": ev.get("description") or ""}


def as_context(p: dict) -> str:
    """Plain text for the local AI to write a short brief from."""
    ev = p["event"]
    lines = [f"Meeting: {ev['title']} on {ev['start'].replace('T', ' ')}"]
    if ev.get("description"):
        lines.append(f"Description: {ev['description'][:400]}")
    if p["people"]:
        lines.append("People: " + ", ".join(p["people"]))
    if p["last"]:
        lines += ["", f"Last time ({p['last']['date']}, {p['last']['title']}): {p['last']['summary'][:600]}"]
    if p["actions"]:
        lines += ["", "Open follow-ups:"] + [f"- {a['text']}" + (f" (owner: {a['owner']})" if a.get("owner") else "")
                                             + (f" (due {a['due']})" if a.get("due") else "") for a in p["actions"][:10]]
    if p["decisions"]:
        lines += ["", "Decisions so far:"] + [f"- {d['text']}" for d in p["decisions"][:8]]
    if p["questions"]:
        lines += ["", "Open questions:"] + [f"- {q['text']}" for q in p["questions"][:6]]
    if p["documents"]:
        lines += ["", "Documents:"] + [f"- {d['title']} ({d['kind']})" for d in p["documents"]]
    return "\n".join(lines)
