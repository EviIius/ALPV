"""Glossary: the acronyms and terms in your notes, and what they stand for.

Everything here is found by rule on this Mac. Nothing is sent anywhere and no AI
is involved in building it:

* "Statistical Analysis System (SAS)" and "SAS (Statistical Analysis System)"
* glossary tables ("| Acronym | Meaning |") and lines such as "SAS – Statistical
  Analysis System", plus any "Term – meaning" line under a Glossary or Acronyms heading
* acronyms you use in several notes but never spell out are listed under
  "Needs a definition"

Your own definitions always win and are never overwritten. "Sounds like" variants
(what a transcription tends to hear, e.g. "Gizmos" for GSMOS) give one-click term
fixes in notes, are passed to local Whisper so recordings come out spelled right,
and are given to the local AI so summaries use the right words.
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter, defaultdict

from .. import db
from ..util import now_ms

# An acronym: 2–8 capitals/digits starting with a letter, optionally plural ("KPIs").
ACRONYM = re.compile(r"(?<![\w&/.\-])([A-Z][A-Z0-9&]{0,6}[A-Z0-9])(s?)(?![\w&\-])")
# The short form inside brackets: "(SAS)", "(SaaS)", "(GSMOS)".
PAREN_SHORT = re.compile(r"\(\s*([A-Za-z][A-Za-z0-9&]{1,9})\s*\)")
# "SAS (Statistical Analysis System)"
SHORT_PAREN = re.compile(r"(?<![\w&])([A-Za-z][A-Za-z0-9&]{1,9})\s+\(([^()\n]{3,160})\)")
# "SAS – Statistical Analysis System", "**SAS**: …", "- SAS = …"
LINE_DEF = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)?(?:\*\*|__)?([A-Za-z][\w&./ \-]{0,38}?)(?:\*\*|__)?\s*(?:[–—:=]|\s-\s)\s*(.{2,300})$")
LIST_ITEM = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)?(?:\*\*|__)")
# A heading that starts a glossary section: "Glossary", "6. Acronyms", "Appendix · Glossary: Platforms".
GLOSSARY_HEADING = re.compile(
    r"^(?:\d+[.)]\s*)?(?:appendix\s*[:·\-–—]?\s*)?(glossary|acronyms|abbreviations|definitions|terminology|key terms|"
    r"terms and definitions|acronyms and (?:abbreviations|definitions|terms)|glossary of terms)\s*(?:$|[:·\-–—(])", re.I)
TABLE_TERM_HEAD = re.compile(r"^(acronyms?|abbreviations?|abbr\.?|terms?|short ?form|codes?|names?)$", re.I)
TABLE_DEF_HEAD = re.compile(r"^(definitions?|meanings?|stands for|descriptions?|full ?(form|name)|expansions?|long ?form|what it means)$", re.I)
# Readings your notes record for a transcript: FCRM ("FICRAM"), Credit 360 ("Credit 362"),
# Yaginesh (FCRM product; transcribed "YAG"), "GRD" is likely eGRC.
_Q = r"[\"“”'‘’]"
QUOTED = re.compile(_Q + r"([^\"“”'‘’\n]{1,40}?)[,.;]?" + _Q)
TERM_RUN = r"(?<![\w&.\-])((?:[A-Z][\w&\-]*|[a-z]+[A-Z][\w&\-]*|\d[\w&\-]*)(?:[ \t]+(?:[A-Z][\w&\-]*|\d[\w&\-]*)){0,3})"
READINGS = re.compile(TERM_RUN + r"\s+\(\s*((?:" + _Q + r"[^\"“”'‘’\n]{1,40}" + _Q + r"[\s,/;]*(?:or\s+|and\s+)?)+)\)")
TRANSCRIBED = re.compile(TERM_RUN + r"\s+\([^()\n]*?\b(?:transcribed|heard|written|spelled)(?: as)?\s+(" + _Q + r"[^\"“”'‘’\n]{1,40}" + _Q + r"(?:\s*(?:,|/|or)\s*" + _Q + r"[^\"“”'‘’\n]{1,40}" + _Q + r")*)")
IS_LIKELY = re.compile(_Q + r"([^\"“”'‘’\n]{1,40})" + _Q + r"\s+(?:is|was|=|means)\s+(?:likely|probably|actually|really|meant to be)?\s*"
                       r"([A-Za-z][\w&\-]*(?:[ \t]+[A-Z][\w&\-]*){0,2})")
TRANSCRIPT_CONTEXT = re.compile(r"transcri|readings?\b|heard|spellings?|recording|auto-generated|misheard|sounds like|said as", re.I)
NOT_TERMS = {"note", "notes", "answer", "question", "host", "speaker", "mike", "unclear", "confirmed", "readings", "readings used",
             "example", "status", "owner", "context", "decision", "action", "next steps", "summary", "update", "q", "a"}

SMALL = {"of", "and", "the", "for", "to", "in", "on", "a", "an", "&", "at", "by", "with", "de", "or", "per", "vs", "via"}

# Words that are written in capitals but aren't the kind of acronym worth listing:
# everyday English, units of time, file types and generic business shorthand.
STOP = set("""
THE AND FOR NOT BUT YOU ARE WAS ALL ANY CAN HAS HAD HER HIS ITS OUR OUT NEW NOW OLD ONE TWO SIX TEN YES NO OK OR IF IN
ON AT TO OF BY IS IT BE DO GO SO UP US WE MY ME HE AN AS AM PM VS ETC NOTE NOTES TODO DONE OPEN CLOSED DRAFT FINAL TBD TBA
NA INFO NEXT STEPS ACTION ACTIONS ITEMS ITEM DATE TIME OWNER STATUS TOTAL HIGH LOW MED WIP FYI ASAP EOD EOW EOM EOY COB
ETA FAQ AKA RE CC BCC FW FWD ID IDS TIP WARNING IMPORTANT AGENDA SUMMARY UPDATE NEW OLD KEY YES NONE TEAM PLAN GOAL GOALS
JAN FEB MAR APR MAY JUN JUL AUG SEP SEPT OCT NOV DEC MON TUE TUES WED THU THUR THURS FRI SAT SUN
UTC GMT EST EDT CST CDT MST MDT PST PDT ET CT MT PT BST CET CEST IST AEST JST
PDF PPT PPTX DOC DOCX XLS XLSX CSV TXT RTF HTML HTM CSS JSON XML YAML URL URI API APIS UI UX IT AI ML LLM USB PC MAC IOS
OS SQL HTTP HTTPS WWW PNG JPG JPEG GIF SVG MP3 MP4 M4A WAV MOV ZIP VPN SSO MFA PIN SMS
CEO CFO COO CTO CIO CISO CMO VP SVP EVP HR PR QA KPI OKR ROI YOY QOQ MOM YTD QTD MTD FY USD EUR GBP CAD AUD LLC INC
LTD CO CORP PTO OOO NDA SOW RFP RFI RFQ SLA POC MVP FTE PMO TV DC NY LA SF UK EU USA
II III IV VI VII VIII IX XI XII
OVERVIEW SUMMARY PURPOSE SCOPE RISK RISKS ISSUE ISSUES DECISION DECISIONS QUESTION QUESTIONS ATTENDEES CONTEXT PROBLEM
RESULTS RESULT DETAILS TITLE SLIDE PAGE TABLE FIGURE CHART SOURCE TOPIC TOPICS WEEK MONTH YEAR TODAY PHASE STEP TASK TASKS
DUE COST COSTS SAVINGS BUDGET PRICE VALUE DATA REPORT REVIEW PROJECT PROGRAM PROCESS POLICY INTERNAL PUBLIC PRIVATE TRUE
FALSE NULL OTHER TOTALS AVG MIN MAX SUM COUNT DRAFTS NOTICE STOP START END TEST DEMO HELP NEEDS MUST SHOULD WILL
WHAT WHY HOW WHO WHEN WHERE WHICH OVERALL FIND GATE PILLAR PILLARS FIRST LAST THEN ALSO ONLY JUST EACH BOTH
""".split())

_lock = threading.Lock()
_state: dict = {"sig": None, "rebuilt_at": None, "hint_re": None, "hint_terms": None}


class GlossaryError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Finding definitions
# ---------------------------------------------------------------------------
def _clean_word(w: str) -> str:
    return w.strip("()[]{}\"'*_,.;:!?`“”‘’")


def _letters(short: str) -> list[str]:
    return [c.lower() for c in short if c.isalnum()]


def spells(short: str, words: list[str]) -> int:
    """How many leading `words` spell `short` by their initials (small words like "of"
    may be skipped, and inner capitals count: "DataHub" → DH). 0 if they don't."""
    letters = _letters(short)
    if len(letters) < 2:
        return 0
    i = 0
    for n, w in enumerate(words, 1):
        for part in [p for p in re.split(r"[-/]", w) if p]:
            p = _clean_word(part)
            if not p:
                continue
            if i < len(letters) and p[0].lower() == letters[i]:
                i += 1
                for ch in p[1:]:
                    if i < len(letters) and (ch.isupper() or ch.isdigit()) and ch.lower() == letters[i]:
                        i += 1
            elif p.lower() in SMALL:
                continue
            else:
                return 0
        if i == len(letters):
            return n
    return 0


def _is_short(token: str) -> bool:
    """Looks like an acronym: at least two capitals, mostly capitals, no spaces."""
    caps = sum(1 for c in token if c.isupper())
    return 2 <= len(token) <= 10 and caps >= 2 and caps >= len([c for c in token if c.isalpha()]) / 2


def _shouting(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 12 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.6


def _tidy_definition(text: str) -> str:
    text = re.sub(r"[*_`]+", "", text or "").strip(" \t-–—:;,.|")
    text = re.sub(r"\s+", " ", text)
    return text[:240]


def _variants(text: str, term: str) -> list[str]:
    """Quoted readings that are real alternatives: not the term itself, its plural, a number or a fragment."""
    out = []
    for v in QUOTED.findall(text or ""):
        v = _singular(_tidy_definition(v))
        low = v.lower()
        if (len(v) < 3 or v.replace(" ", "").isdigit() or low in (term.lower(), term.lower() + "s")
                or low in term.lower().split() or len(v.split()) > 4):
            continue
        out.append(v)
    return out


def _singular(term: str) -> str:
    return term[:-1] if term.endswith("s") and _is_short(term[:-1]) and len(term) > 3 else term


def _term_ok(term: str) -> bool:
    t = term.strip()
    return bool(t) and len(t) <= 40 and len(t.split()) <= 4 and t.lower() not in NOT_TERMS and not t.lower().startswith(("http", "see "))


def _inline(text: str, out: list) -> None:
    """Definitions and readings stated inside a sentence or table cell."""
    # Long Form (LF)
    for m in PAREN_SHORT.finditer(text):
        short = m.group(1)
        if not _is_short(short):
            continue
        before = re.split(r"[.;:!?()\[\]|]", text[: m.start()])[-1].split()
        for k in range(1, min(len(before), len(_letters(short)) + 4) + 1):
            words = before[-k:]
            if _clean_word(words[0]).lower() in SMALL:
                continue
            if spells(short, words) == k:
                if k > 1 or not words[0].strip("()[]*_").isupper():  # "ORCS (ORC)" is two acronyms, not a definition
                    out.append((short, _tidy_definition(" ".join(words)), []))
                break
    # LF (Long Form), or LF ("how it was heard")
    for m in SHORT_PAREN.finditer(text):
        short, inside = m.group(1), m.group(2)
        if not _is_short(short) or QUOTED.search(inside):
            continue
        words = re.split(r"[,;:]", inside)[0].split()
        while words and _clean_word(words[0]).lower() in SMALL:
            words = words[1:]
        if words and spells(short, words) == len(words) and (len(words) > 1 or not _clean_word(words[0]).isupper()):
            out.append((short, _tidy_definition(" ".join(words)), []))
    # Readings only count where the text is about a transcript ("Readings used: FCRM ("FICRAM")").
    if not TRANSCRIPT_CONTEXT.search(text):
        return
    for m in READINGS.finditer(text):
        term = _singular(_tidy_definition(m.group(1)))
        al = _variants(m.group(2), term)
        if al and _term_ok(term):
            out.append((term, "", al))
    for m in TRANSCRIBED.finditer(text):
        term = _singular(_tidy_definition(m.group(1)))
        al = _variants(m.group(2), term)
        if al and _term_ok(term):
            out.append((term, "", al))
    for m in IS_LIKELY.finditer(text):
        term = _tidy_definition(m.group(2))
        al = _variants(f'"{m.group(1)}"', term)
        if al and _term_ok(term) and (_is_short(term) or term[:1].isupper()):
            out.append((term, "", al))


def find_definitions(text: str) -> list[tuple[str, str, list[str]]]:
    """(term, definition, sounds-like variants) stated in a piece of text. The definition may be ""
    when the text only records how a term was heard."""
    out: list[tuple[str, str, list[str]]] = []
    in_glossary = False
    table: dict | None = None  # {"glossary": bool, "head": (term header, definition header)}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            table = None
            continue
        bare = re.sub(r"^[#\s*_\-•]+|[*_]+$", "", line).strip()
        # Headings switch "glossary mode" on or off.
        if (line.startswith("#") or (len(line) < 60 and line.endswith(":") and len(line.split()) <= 5)
                or re.fullmatch(r"(\*\*|__)[^*_]{2,60}(\*\*|__):?", line)):
            in_glossary = bool(GLOSSARY_HEADING.match(bare.rstrip(":")))
            table = None
            continue
        if GLOSSARY_HEADING.match(bare) and len(bare) < 70:
            in_glossary = True  # "- Glossary: Platforms and Data Terms" on a slide
            table = None
            continue
        # Markdown tables
        if line.startswith("|") and line.count("|") >= 3:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            c0, c1 = (re.sub(r"[*_]", "", cells[0]).strip(), re.sub(r"[*_]", "", cells[1]).strip() if len(cells) > 1 else "")
            header = bool(TABLE_TERM_HEAD.match(c0) and TABLE_DEF_HEAD.match(c1))
            if table is None or header:
                table = {"glossary": header or (in_glossary and len(cells) == 2)}
                if header:
                    continue
            term, definition = _tidy_definition(cells[0]), cells[1] if len(cells) > 1 else ""
            if term and definition:
                m = re.fullmatch(r"(.+?)\s*\(([A-Za-z][A-Za-z0-9&]{1,9})\)", term)
                if m and _is_short(m.group(2)) and spells(m.group(2), m.group(1).split()) == len(m.group(1).split()):
                    # "System of record (SOR) | The application where …"
                    out.append((m.group(2), _tidy_definition(f"{m.group(1)}: {definition}"), []))
                elif table["glossary"] and _term_ok(term):
                    out.append((term, _tidy_definition(definition), []))
                elif _is_short(term):
                    words = re.split(r"[,.;:(]", _tidy_definition(definition))[0].split()
                    n = spells(term, words)
                    if n and (n == len(words) or all(w[:1].isupper() or w.lower() in SMALL for w in words[:n])):
                        out.append((term, _tidy_definition(" ".join(words[:n])), []))
            for cell in cells:
                _inline(cell, out)
            continue
        table = None
        _inline(line, out)
        # "LF – Long Form" lines
        m = LINE_DEF.match(line)
        if m:
            term, rest = _tidy_definition(m.group(1)), m.group(2)
            if in_glossary and (LIST_ITEM.match(line) or re.match(r"^\s*(?:[-*•]|\d+[.)])\s+", line)) and _term_ok(term):
                out.append((term, _tidy_definition(rest), []))
            elif _is_short(term):
                words = re.split(r"[,.;:(]", _tidy_definition(rest))[0].split()
                n = spells(term, words)
                # "SAS – Statistical Analysis System used for reporting": the capitalised words that spell it.
                if n and (n == len(words) or all(w[:1].isupper() or w.lower() in SMALL for w in words[:n])):
                    out.append((term, _tidy_definition(" ".join(words[:n])), []))
    return out


def acronyms_in(text: str) -> Counter:
    """Acronym uses in a piece of text (plurals folded: KPIs → KPI), skipping headings and shouted lines."""
    c: Counter = Counter()
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or _shouting(s) or ACRONYM.fullmatch(s.strip("*_:").strip()):
            continue
        for m in ACRONYM.finditer(line):
            tok = m.group(1)
            if sum(1 for ch in tok if ch.isupper()) >= 2:
                c[tok] += 1
    return c


# ---------------------------------------------------------------------------
# Rebuilding the glossary from your notes
# ---------------------------------------------------------------------------
def _dictionary_words(candidates: set[str]) -> set[str]:
    """Which candidates are ordinary words, per the system word list (macOS has one)."""
    if not candidates:
        return set()
    hits: set[str] = set()
    for path in ("/usr/share/dict/words", "/usr/dict/words"):
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for w in f:
                    w = w.strip().lower()
                    if w in candidates:
                        hits.add(w)
            break
        except OSError:
            continue
    return hits


def _ordinary_words(rows: list[dict], candidates: set[str], uses: Counter) -> set[str]:
    """Capitalised words your notes also write in lower case ("EXAMPLES"/"examples") are ordinary words."""
    if not candidates:
        return set()
    pat = re.compile(r"(?<![\w&\-])(" + "|".join(re.escape(c) for c in sorted(candidates, key=len, reverse=True)) + r")s?(?![\w&\-])")
    lower: Counter = Counter()
    for r in rows:
        for m in pat.finditer(r["body"] or ""):
            lower[m.group(1)] += 1
    return {c for c in candidates if lower[c] >= max(2, uses[c] // 5)}


def _signature() -> tuple:
    r = db.q1("SELECT count(*) AS n, COALESCE(max(updated_at), 0) AS t FROM notes WHERE trashed_at IS NULL")
    return (r["n"], r["t"])


def rebuild(force: bool = False) -> dict:
    """Re-read every unlocked note and refresh the found entries. Cheap when nothing changed."""
    with _lock:
        sig = _signature()
        if not force and sig == _state["sig"]:
            return {"skipped": True}
        defs: dict[str, Counter] = defaultdict(Counter)
        readings: dict[str, dict[str, str]] = defaultdict(dict)
        shown: dict[tuple[str, str], str] = {}
        source: dict[str, str] = {}
        uses: Counter = Counter()
        in_notes: dict[str, set] = defaultdict(set)
        rows = db.q("SELECT id, title, body FROM notes WHERE trashed_at IS NULL AND locked = 0")
        for r in rows:
            text = f"{r['title'] or ''}\n{r['body'] or ''}"
            for term, definition, variants in find_definitions(text):
                key, dkey = term.lower(), definition.lower()
                shown.setdefault((key, ""), term)
                for v in variants:
                    readings[key].setdefault(v.lower(), v)
                if not definition or dkey == key:
                    continue
                defs[key][dkey] += 1
                shown.setdefault((key, dkey), definition)
                source.setdefault(key, r["id"])
            for tok, n in acronyms_in(text).items():
                uses[tok.lower()] += n
                in_notes[tok.lower()].add(r["id"])
                shown.setdefault((tok.lower(), ""), tok)

        existing = {r["term"].lower(): r for r in db.q("SELECT * FROM glossary")}
        # Mixed-case and multi-word terms (yours, or from glossary tables) are counted separately.
        extra = [t for t in set(defs) | set(readings) | {k for k, r in existing.items() if r["origin"] == "user"} if t not in uses]
        if extra:
            pat = re.compile(r"(?<![\w])(" + "|".join(re.escape(t) for t in sorted(extra, key=len, reverse=True)) + r")(?![\w])", re.I)
            for r in rows:
                for m in pat.finditer(f"{r['title'] or ''}\n{r['body'] or ''}"):
                    k = m.group(1).lower()
                    uses[k] += 1
                    in_notes[k].add(r["id"])

        candidates = {k for k, n in uses.items() if k not in defs and k not in readings and n >= 3 and len(in_notes[k]) >= 2
                      and len(k) >= 3 and k.upper() not in STOP}
        words = _dictionary_words({k for k in candidates if len(k) >= 4}) | _ordinary_words(rows, candidates, uses)
        found: dict[str, dict] = {}
        for key, counter in defs.items():
            dkey, _ = sorted(counter.items(), key=lambda kv: (-kv[1], len(kv[0])))[0]
            found[key] = {"term": shown.get((key, ""), key), "definition": shown[(key, dkey)], "source_note": source.get(key)}
        for key, variants in readings.items():
            found.setdefault(key, {"term": shown.get((key, ""), key), "definition": "", "source_note": None})
            found[key]["aliases"] = sorted(variants.values(), key=str.lower)[:12]
        for key, n in uses.items():
            if key in found or key.upper() in STOP or key in words or len(key) < 3 or any(ch.isdigit() for ch in key):
                continue
            if n >= 3 and len(in_notes[key]) >= 2:
                found[key] = {"term": shown.get((key, ""), key.upper()), "definition": "", "source_note": None}

        ts = now_ms()
        added = updated = removed = 0
        with db.tx() as c:
            for key, f in found.items():
                row = existing.get(key)
                u, nc = uses.get(key, 0), len(in_notes.get(key, ()))
                al = json.dumps(f.get("aliases") or [])
                if row is None:
                    c.execute("""INSERT INTO glossary(term, definition, aliases, source_note, origin, hidden, uses, note_count, created_at, updated_at)
                                 VALUES (?,?,?,?,?,?,?,?,?,?)""",
                              (f["term"], f["definition"], al, f["source_note"], "found", 0, u, nc, ts, ts))
                    added += 1
                elif row["origin"] == "user":
                    if (row["uses"], row["note_count"]) != (u, nc):
                        c.execute("UPDATE glossary SET uses = ?, note_count = ? WHERE term = ?", (u, nc, row["term"]))
                else:
                    if (row["definition"], row["aliases"], row["source_note"], row["uses"], row["note_count"]) != (
                            f["definition"], al, f["source_note"], u, nc):
                        c.execute("""UPDATE glossary SET definition = ?, aliases = ?, source_note = ?, uses = ?, note_count = ?, updated_at = ?
                                     WHERE term = ?""", (f["definition"], al, f["source_note"], u, nc, ts, row["term"]))
                        updated += 1
            for key, row in existing.items():
                if key in found:
                    continue
                if row["origin"] == "user":
                    u, nc = uses.get(key, 0), len(in_notes.get(key, ()))
                    if (row["uses"], row["note_count"]) != (u, nc):
                        c.execute("UPDATE glossary SET uses = ?, note_count = ? WHERE term = ?", (u, nc, row["term"]))
                elif not row["hidden"]:
                    c.execute("DELETE FROM glossary WHERE term = ?", (row["term"],))
                    removed += 1
                elif row["uses"]:
                    c.execute("UPDATE glossary SET uses = 0, note_count = 0 WHERE term = ?", (row["term"],))
        _state.update(sig=sig, rebuilt_at=ts, hint_re=None, hint_terms=None)
        return {"added": added, "updated": updated, "removed": removed, "terms": len(found)}


def invalidate() -> None:
    """Forget cached lookups (after a note is locked, or the glossary changes)."""
    _state.update(sig=None, hint_re=None, hint_terms=None)


# ---------------------------------------------------------------------------
# Reading and editing
# ---------------------------------------------------------------------------
def excerpts(term: str, limit: int = 6, width: int = 380) -> list[dict]:
    """Passages from unlocked notes that use a term: the evidence the local AI may read to suggest a meaning."""
    term = (term or "").strip()
    if not term:
        return []
    pat = re.compile(r"(?<![\w&])" + re.escape(term) + r"(?:s|'s)?(?![\w&])", re.I if not term.isupper() else 0)
    rows = db.q("""SELECT id, title, body FROM notes WHERE trashed_at IS NULL AND locked = 0
                   AND (body LIKE ? OR title LIKE ?) ORDER BY updated_at DESC LIMIT 60""", (f"%{term}%", f"%{term}%"))
    out = []
    for r in rows:
        body = r["body"] or ""
        m = pat.search(body)
        if not m:
            continue
        start = max(0, m.start() - width // 2)
        end = min(len(body), m.end() + width // 2)
        if start:
            start = body.find(" ", start, m.start()) + 1 or start
        if end < len(body):
            end = body.rfind(" ", m.end(), end) if body.rfind(" ", m.end(), end) > 0 else end
        text = re.sub(r"\s+", " ", body[start:end]).strip()
        if len(text) < len(term) + 12:
            continue
        out.append({"note_id": r["id"], "title": r["title"] or "Untitled", "text": text})
        if len(out) >= limit:
            break
    return out


def to_api(r: dict) -> dict:
    return {"term": r["term"], "definition": r["definition"], "aliases": json.loads(r["aliases"] or "[]"),
            "origin": r["origin"], "hidden": bool(r["hidden"]), "uses": r["uses"], "note_count": r["note_count"],
            "source_note": r["source_note"], "source_title": r.get("source_title"), "updated_at": r["updated_at"]}


def list_terms(q: str = "", show: str = "all", limit: int = 500) -> dict:
    where, params = [], []
    if show == "hidden":
        where.append("g.hidden = 1")
    else:
        where.append("g.hidden = 0")
        if show == "undefined":
            where.append("g.definition = '' AND g.aliases = '[]'")
        elif show == "spellings":
            where.append("g.aliases != '[]'")
        elif show == "defined":
            where.append("g.definition != ''")
        elif show == "yours":
            where.append("g.origin = 'user'")
    if q.strip():
        where.append("(g.term LIKE ? OR g.definition LIKE ? OR g.aliases LIKE ?)")
        like = f"%{q.strip()}%"
        params += [like, like, like]
    rows = db.q(f"""SELECT g.*, n.title AS source_title FROM glossary g
                    LEFT JOIN notes n ON n.id = g.source_note AND n.trashed_at IS NULL AND n.locked = 0
                    WHERE {' AND '.join(where)}
                    ORDER BY (g.definition = '' AND g.aliases = '[]'), (g.definition = ''), g.uses DESC, g.term COLLATE NOCASE
                    LIMIT ?""", (*params, limit))
    counts = db.q1("""SELECT sum(hidden = 0) AS all_, sum(hidden = 0 AND definition = '' AND aliases = '[]') AS undefined,
                             sum(hidden = 0 AND aliases != '[]') AS spellings,
                             sum(hidden = 0 AND origin = 'user') AS yours, sum(hidden = 1) AS hidden FROM glossary""") or {}
    return {"items": [to_api(r) for r in rows],
            "counts": {"all": counts.get("all_") or 0, "undefined": counts.get("undefined") or 0, "spellings": counts.get("spellings") or 0,
                       "yours": counts.get("yours") or 0, "hidden": counts.get("hidden") or 0},
            "rebuilt_at": _state["rebuilt_at"]}


def _clean_aliases(aliases, term: str) -> list[str]:
    if aliases is None:
        return []
    if isinstance(aliases, str):
        aliases = re.split(r"[,;\n]", aliases)
    out: list[str] = []
    for a in aliases[:12]:
        a = re.sub(r"\s+", " ", str(a)).strip()[:40]
        if a and a.lower() != term.lower() and a.lower() not in (x.lower() for x in out):
            out.append(a)
    return out


def save(term: str, definition: str | None = None, aliases=None, *, rename: str | None = None) -> dict:
    """Add or change a term. Anything you edit becomes yours and is never overwritten."""
    term = re.sub(r"\s+", " ", (term or "")).strip()
    if not term or len(term) > 60:
        raise GlossaryError("A term needs 1 to 60 characters.")
    ts = now_ms()
    with _lock, db.tx() as c:
        row = c.execute("SELECT * FROM glossary WHERE term = ?", (term,)).fetchone()
        row = dict(row) if row else None
        new_term = re.sub(r"\s+", " ", (rename or term)).strip()[:60] or term
        d = row["definition"] if (row and definition is None) else re.sub(r"\s+", " ", (definition or "")).strip()[:400]
        al = json.loads(row["aliases"]) if (row and aliases is None) else _clean_aliases(aliases, new_term)
        if row:
            if new_term.lower() != term.lower() and c.execute("SELECT 1 FROM glossary WHERE term = ?", (new_term,)).fetchone():
                raise GlossaryError(f"{new_term} is already in your glossary.")
            c.execute("""UPDATE glossary SET term = ?, definition = ?, aliases = ?, origin = 'user', hidden = 0, updated_at = ?
                         WHERE term = ?""", (new_term, d, json.dumps(al), ts, row["term"]))
        else:
            c.execute("""INSERT INTO glossary(term, definition, aliases, source_note, origin, hidden, uses, note_count, created_at, updated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?)""", (new_term, d, json.dumps(al), None, "user", 0, 0, 0, ts, ts))
        _state.update(hint_re=None, hint_terms=None, sig=None)
    return get(new_term)


def get(term: str) -> dict:
    r = db.q1("""SELECT g.*, n.title AS source_title FROM glossary g
                 LEFT JOIN notes n ON n.id = g.source_note AND n.trashed_at IS NULL AND n.locked = 0 WHERE g.term = ?""", (term,))
    if not r:
        raise KeyError(term)
    return to_api(r)


def set_hidden(term: str, hidden: bool) -> dict:
    with db.tx() as c:
        if not c.execute("UPDATE glossary SET hidden = ?, updated_at = ? WHERE term = ?", (1 if hidden else 0, now_ms(), term)).rowcount:
            raise KeyError(term)
    _state.update(hint_re=None, hint_terms=None)
    return get(term)


def remove(term: str) -> dict:
    """Your own terms are deleted; found ones are hidden so the next scan doesn't bring them back."""
    r = db.q1("SELECT * FROM glossary WHERE term = ?", (term,))
    if not r:
        raise KeyError(term)
    if r["origin"] == "user" and not r["source_note"]:
        with db.tx() as c:
            c.execute("DELETE FROM glossary WHERE term = ?", (r["term"],))
        _state.update(hint_re=None, hint_terms=None, sig=None)
        return {"deleted": True}
    set_hidden(r["term"], True)
    return {"hidden": True}


def definitions_map(limit: int = 3000) -> list[dict]:
    """Defined, visible terms for hover definitions in the app."""
    return [{"term": r["term"], "definition": r["definition"]} for r in db.q(
        "SELECT term, definition FROM glossary WHERE hidden = 0 AND definition != '' ORDER BY uses DESC LIMIT ?", (limit,))]


# ---------------------------------------------------------------------------
# Using it: AI hints, Whisper vocabulary and "sounds like" fixes
# ---------------------------------------------------------------------------
def _hint_index():
    if _state["hint_re"] is None:
        rows = db.q("SELECT term, definition, aliases FROM glossary WHERE hidden = 0 AND (definition != '' OR aliases != '[]')")
        terms = {}
        for r in rows:
            terms[r["term"].lower()] = (r["term"], r["definition"], json.loads(r["aliases"] or "[]"))
            for a in json.loads(r["aliases"] or "[]"):
                terms.setdefault(a.lower(), (r["term"], r["definition"], json.loads(r["aliases"] or "[]")))
        if terms:
            alts = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
            _state["hint_re"] = re.compile(rf"(?<![\w])({alts})(?![\w])", re.I)
        else:
            _state["hint_re"] = False
        _state["hint_terms"] = terms
    return _state["hint_re"], _state["hint_terms"]


def hint(text: str, limit: int = 15) -> str:
    """A short glossary for the local AI: the terms that appear in `text`, most used first."""
    try:
        pat, terms = _hint_index()
    except Exception:  # noqa: BLE001 - the AI works fine without it
        return ""
    if not pat or not text:
        return ""
    seen: dict[str, tuple] = {}
    for m in pat.finditer(text[:200_000]):
        hit = m.group(1)
        t = terms.get(hit.lower())
        # Acronyms only count in capitals ("IT", not "it"); "sounds like" variants match any case.
        if t and hit.lower() == t[0].lower() and _is_short(t[0]) and hit != t[0]:
            continue
        if t and t[0] not in seen:
            seen[t[0]] = t
            if len(seen) >= limit:
                break
    if not seen:
        return ""
    lines = []
    for term, definition, aliases in seen.values():
        line = f"- {term}" + (f" = {definition}" if definition else "")
        if aliases:
            line += f" (a transcript may write it as: {', '.join(aliases[:4])})"
        lines.append(line)
    return "Terms used in this material (from the user's glossary; use these exact spellings):\n" + "\n".join(lines)


def vocabulary(max_chars: int = 600) -> str:
    """Prompt for local Whisper so recordings come out with the right spellings."""
    try:
        rows = db.q("""SELECT term FROM glossary WHERE hidden = 0 AND (definition != '' OR origin = 'user' OR aliases != '[]')
                       ORDER BY (aliases != '[]') DESC, uses DESC LIMIT 80""")
    except Exception:  # noqa: BLE001
        return ""
    out, size = [], 0
    for r in rows:
        if size + len(r["term"]) + 2 > max_chars:
            break
        out.append(r["term"])
        size += len(r["term"]) + 2
    return ("Glossary: " + ", ".join(out) + ".") if out else ""


def distinctive(variant: str) -> bool:
    """A reading unlikely to be an ordinary word: has a digit or at least two capitals ("FICRAM", "Credit 362")."""
    return bool(re.search(r"\d", variant)) or sum(1 for c in variant if c.isupper()) >= 2


def _alias_rows() -> list[tuple[str, list[str]]]:
    """Variants offered as fixes: all of yours; found ones only when distinctive, so ordinary words
    a transcript once produced (e.g. "Sure") are never suggested as mistakes."""
    out = []
    for r in db.q("SELECT term, aliases, origin FROM glossary WHERE hidden = 0 AND aliases != '[]'"):
        al = json.loads(r["aliases"] or "[]")
        if r["origin"] != "user":
            al = [a for a in al if distinctive(a)]
        if al:
            out.append((r["term"], al))
    return out


def _matches(text: str, term: str, variant: str):
    """Places where `variant` is written, leaving alone the term itself (e.g. "360" inside "Credit 360")."""
    if len(variant) < 3 or variant.replace(" ", "").isdigit():
        return
    taken = [m.span() for m in re.finditer(rf"(?<![\w]){re.escape(term)}(?![\w])", text, re.I)]
    for m in re.finditer(rf"(?<![\w]){re.escape(variant)}(?![\w])", text, re.I):
        if m.group(0) == term or any(a < m.end() and m.start() < b for a, b in taken):
            continue
        yield m


def fixes(text: str) -> list[dict]:
    """"Sounds like" variants written in a note: [{wrong, variant, term, count}]."""
    out = []
    for term, aliases in _alias_rows():
        for a in aliases:
            found = [m.group(0) for m in _matches(text or "", term, a)]
            if found:
                out.append({"wrong": found[0], "variant": a, "term": term, "count": len(found)})
    out.sort(key=lambda x: -x["count"])
    return out


def apply_fixes(text: str, pairs: list[dict]) -> tuple[str, int]:
    """Replace the chosen variants with their terms. Returns (new text, replacements)."""
    allowed = {a.lower(): (term, a) for term, aliases in _alias_rows() for a in aliases}
    spans: list[tuple[int, int, str]] = []
    for p in pairs or []:
        hit = allowed.get(str(p.get("variant") or p.get("wrong") or "").lower())
        if not hit or (p.get("term") and str(p["term"]).lower() != hit[0].lower()):
            continue
        term, variant = hit
        for m in _matches(text, term, variant):
            if not any(a < m.end() and m.start() < b for a, b, _ in spans):
                spans.append((m.start(), m.end(), term))
    for a, b, term in sorted(spans, reverse=True):
        text = text[:a] + term + text[b:]
    return text, len(spans)
