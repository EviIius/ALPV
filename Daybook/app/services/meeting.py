"""Meeting notes, read by rules: the details table, people, decisions, action items
and open questions. Works on the Markdown that Word documents convert to (and on
notes written in Daybook), so no AI is needed for well-structured notes.

    | Item | Detail |              # 7. Decisions            # 8. Action items
    | Meeting date | Fri, Oct 9 |   | # | Decision |            | Action | Owner | Notes |
    | Attendees | Laura, Carl… |   | 1 | BDL keeps the list |   | Set up a session | Kelly | … |
"""
from __future__ import annotations

import re
from datetime import date

from . import datescan

PEOPLE_LABELS = re.compile(
    r"attend|participant|present\b|presenting|presenter|team\b|bdl\b|owner|speaker|ran the|led by|lead\b|host|"
    r"facilitat|organi[sz]er|^with\b|people|who\b|workspace side|interviewer|interviewee|panel|guests?",
    re.I,
)
NOT_PEOPLE_LABELS = re.compile(r"referenced|mentioned|format|purpose|date|time|location|topic|agenda|note on", re.I)
SECTION = {
    "decisions": re.compile(r"^\s*(?:\d+[.)]\s*)?(?:key\s+)?decisions?(?:\s+made)?\b", re.I),
    "actions": re.compile(r"^\s*(?:\d+[.)]\s*)?(?:action\s*items?|actions|next\s+steps|follow[- ]?ups?|to-?dos?|tasks)\b", re.I),
    "questions": re.compile(r"^\s*(?:\d+[.)]\s*)?(?:open\s+(?:questions|items|issues)|questions|parking\s+lot|risks?\s+and\s+open)", re.I),
    "summary": re.compile(r"^\s*(?:\d+[.)]\s*)?(?:summary|tl;?dr|in short|overview)\b", re.I),
}
NAME_STOP = {
    "others", "other", "team", "teams", "and", "the", "from", "context", "plus", "several", "various", "everyone",
    "all", "group", "na", "n/a", "tbd", "none", "unknown", "me", "myself", "us", "we", "out", "led", "facilitator",
    "host", "owner", "owners", "lead", "notes", "not", "present", "partway", "joined", "late", "call", "meeting",
    # organisation words that look like names in attendee lines
    "risk", "model", "management", "governance", "consumer", "power", "bi", "workstream", "technology", "tech", "data",
    "platform", "finance", "financial", "operations", "ops", "legal", "compliance", "audit", "security", "engineering",
    "product", "program", "project", "business", "corporate", "enterprise", "strategy", "analytics", "reporting",
    "support", "admin", "admins", "leadership", "executive", "executives", "committee", "board", "council", "office",
    "division", "department", "org", "organization", "vendor", "vendors", "client", "clients", "customer", "customers",
    "partner", "partners", "stakeholders", "presenter", "presenters", "attendees", "participants", "users", "user",
    "pos", "po", "pm", "pms", "architects", "architect", "analysts", "analyst", "lob", "lobs", "line", "lines",
}
_CELL_SPLIT = re.compile(r"(?<!\\)\|")


def _cells(line: str) -> list[str]:
    s = line.strip()
    if not (s.startswith("|") and s.endswith("|")):
        return []
    return [re.sub(r"[*_`]", "", c).strip() for c in _CELL_SPLIT.split(s[1:-1])]


def _is_sep(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c.replace(" ", "")) for c in cells if c)


def _tables(lines: list[str]) -> list[list[list[str]]]:
    out, cur = [], []
    for line in lines:
        cells = _cells(line)
        if cells:
            if not _is_sep(cells):
                cur.append(cells)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _bullets(lines: list[str]) -> list[str]:
    items = []
    for line in lines:
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(?:\[[ xX]\]\s+)?(.+)$", line)
        if m:
            items.append(re.sub(r"[*_`]", "", m.group(1)).strip())
    return items


def sections(md: str) -> list[tuple[str, int, list[str]]]:
    """[(heading, level, lines)] — the preamble has heading ''."""
    out: list[tuple[str, int, list[str]]] = [("", 0, [])]
    for line in (md or "").splitlines():
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if m:
            out.append((re.sub(r"[*_`]", "", m.group(2)).strip(), len(m.group(1)), []))
        else:
            out[-1][2].append(line)
    return out


def split_names(value: str) -> list[str]:
    v = re.sub(r"\([^)]*\)", " ", value or "")          # "(FCRM)", "(led)"
    v = re.sub(r"\b(?:and|&|plus|with)\b", ",", v, flags=re.I)
    v = re.sub(r"\b(?:e\.g\.|i\.e\.|etc\.?)", " ", v, flags=re.I)
    names: list[str] = []
    for part in re.split(r"[,;/·•]|\s{2,}|\s-\s", v):
        p = part.strip(" .:'\"’")
        p = re.sub(r"^(?:the|a|an|our|their|from|by)\s+", "", p, flags=re.I)
        p = re.sub(r"\s+(?:out|only|joined late|on another call)$", "", p, flags=re.I)
        if not p or len(p) > 40:
            continue
        words = p.split()
        if len(words) > 3 or any(w.lower() in NAME_STOP for w in words):
            continue
        if not all(re.fullmatch(r"[A-Z][a-zA-Z'’.\-]*", w) for w in words):
            continue
        if len(words) == 1 and words[0].isupper() and len(words[0]) > 2:   # acronyms like BDL, FCRM
            continue
        if any(w.endswith(("'s", "’s", "'", "’")) for w in words):          # "Kevin's" (a team, not a person)
            continue
        if p not in names:
            names.append(p)
    return names


def _find_col(header: list[str], rx: str) -> int | None:
    for i, h in enumerate(header):
        if re.search(rx, h, re.I):
            return i
    return None


def extract(md: str, *, ref: date | None = None) -> dict | None:
    """Structured facts for a meeting-style note, or None when there's nothing to say."""
    if not md:
        return None
    secs = sections(md)
    fields: list[dict] = []
    people: list[str] = []
    decisions: list[str] = []
    actions: list[dict] = []
    questions: list[str] = []
    summary = ""

    def add_people(names):
        for n in names:
            if n not in people:
                people.append(n)

    # The "Item | Detail" table: usually first, sometimes under a "Details" heading.
    for heading, _level, lines in secs[:3]:
        for table in _tables(lines):
            if len(table) < 2 or len(table[0]) != 2:
                continue
            head = [c.lower() for c in table[0]]
            rows = table[1:] if head[0] in ("item", "field", "detail", "") or head == ["item", "detail"] else table
            for label, value in (r for r in rows if len(r) == 2):
                if not label or not value:
                    continue
                fields.append({"label": label[:60], "value": value[:400]})
                if PEOPLE_LABELS.search(label) and not NOT_PEOPLE_LABELS.search(label):
                    add_people(split_names(value))
            if fields:
                break
        if fields:
            break
    # "- **Attendees:** Kelly, Jake" style
    if not fields:
        for line in "\n".join(secs[0][2] + (secs[1][2] if len(secs) > 1 else [])).splitlines()[:40]:
            m = re.match(r"^\s*(?:[-*]\s+)?\**([A-Za-z][A-Za-z ]{1,30}?)\**\s*:\s*\**\s*(.+)$", line)
            if m and len(m.group(2)) < 400:
                label, value = m.group(1).strip(), re.sub(r"[*_`]", "", m.group(2)).strip()
                fields.append({"label": label, "value": value})
                if PEOPLE_LABELS.search(label) and not NOT_PEOPLE_LABELS.search(label):
                    add_people(split_names(value))

    for heading, _level, lines in secs[1:]:
        if SECTION["decisions"].search(heading):
            for table in _tables(lines):
                if not table:
                    continue
                col = _find_col(table[0], r"decision|what|outcome") if len(table[0]) > 1 else 0
                col = col if col is not None else len(table[0]) - 1
                for row in table[1:]:
                    if col < len(row) and row[col]:
                        decisions.append(row[col][:400])
            decisions += [b[:400] for b in _bullets(lines)]
        elif SECTION["actions"].search(heading):
            for table in _tables(lines):
                if len(table) < 2:
                    continue
                h = table[0]
                c_text = _find_col(h, r"action|task|item|what|next step|follow") or 0
                c_owner = _find_col(h, r"owner|who|assign|responsible|lead")
                c_due = _find_col(h, r"due|when|date|by\b|timing|deadline")
                c_notes = _find_col(h, r"note|status|detail|comment")
                for row in table[1:]:
                    text = row[c_text] if c_text < len(row) else ""
                    if not text:
                        continue
                    owner = row[c_owner] if c_owner is not None and c_owner < len(row) else ""
                    due_raw = row[c_due] if c_due is not None and c_due < len(row) else ""
                    actions.append(_action(text, owner, due_raw, row[c_notes] if c_notes is not None and c_notes < len(row) else "", ref))
            for line in lines:
                m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(\[[ xX]\]\s+)?(.+)$", line)
                if not m:
                    continue
                text = re.sub(r"[*_`]", "", m.group(2)).strip()
                done = bool(m.group(1) and "x" in m.group(1).lower())
                owner = ""
                om = re.search(r"\(([A-Z][\w'’ .&/-]{1,40})\)\s*$|\s[—–-]\s*([A-Z][\w'’ .&/-]{1,40})$", text)
                if om:
                    owner = (om.group(1) or om.group(2) or "").strip()
                a = _action(text, owner, "", "", ref)
                a["done"] = done
                actions.append(a)
        elif SECTION["questions"].search(heading):
            questions += [b[:300] for b in _bullets(lines)]
            for table in _tables(lines):
                for row in table[1:]:
                    if row and row[-1]:
                        questions.append(row[0 if len(row) == 1 else -1][:300])
        elif SECTION["summary"].search(heading) and not summary:
            text = " ".join(l.strip() for l in lines if l.strip() and not l.strip().startswith("|"))
            summary = re.sub(r"[*_`]", "", re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)).strip()[:600]

    for a in actions:
        add_people(split_names(a.get("owner") or ""))
    if not (fields or decisions or actions or questions):
        return None
    return {
        "fields": fields[:20],
        "people": people[:40],
        "decisions": decisions[:40],
        "actions": actions[:60],
        "questions": questions[:30],
        "summary": summary,
    }


def _action(text: str, owner: str, due_raw: str, notes: str, ref: date | None) -> dict:
    due = None
    for src in (due_raw, text):
        if not src:
            continue
        found = datescan.find_dates(src, ref)
        if found:
            due = found[0].start.isoformat()
            break
    status_done = bool(re.search(r"\b(done|complete[d]?|closed|finished)\b", notes or "", re.I))
    return {"text": re.sub(r"\s+", " ", text)[:300], "owner": (owner or "").strip()[:80], "due": due,
            "notes": (notes or "").strip()[:200], "done": status_done}
