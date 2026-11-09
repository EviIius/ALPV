"""Date scrubber: work out which day a document belongs to.

Looks at the filename, the title, labelled lines like "Meeting date: Friday,
October 9, 2026", the opening lines, document metadata, when the file was added
to git and when it was last modified. Each clue gets a score, clues that agree
reinforce each other, and the result says how sure it is:

* ``set``        confident; the document goes on the calendar
* ``review``     confident, but some clues disagree, so it's worth a look
* ``suggested``  a reasonable guess that needs a yes/no
* ``none``       nothing to go on

Decisions made by you, the local AI or Claude are kept next to the files in
``<library>/.daybook/dates.json`` and always win.

This module only uses the standard library, so the ``daybook-dates`` command
works without Daybook's virtual environment (for example from a terminal, or
when Claude runs it against the folder).
"""
from __future__ import annotations

import html as _html
import json
import os
import re
import subprocess
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

SIDECAR_DIR = ".daybook"
SIDECAR_FILE = "dates.json"
CONFIG_FILE = "library.json"

SET_AT = 0.75        # at or above: goes on the calendar
SUGGEST_AT = 0.40    # at or above: offered as a suggestion

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}

_MON = (r"(?<![A-Za-z])(?P<{n}>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
        r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?(?![A-Za-z])")
_WD = (r"(?:(?<![A-Za-z])(?P<wd>mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|"
       r"sat(?:urday)?|sun(?:day)?)\.?,?\s+(?:the\s+)?)?")
_DAY = r"(?P<{n}>3[01]|[12]\d|0?[1-9])(?:st|nd|rd|th)?(?![\d])"
_YEAR = r"(?P<y>(?:19|20)\d{2})(?!\d)"
_DASH = r"\s*(?:-|–|—|to|through|thru)\s*"

RX_ISO = re.compile(
    r"(?<![\dA-Za-z.])(?P<y>(?:19|20)\d{2})(?P<sep>[-_./])(?P<m>1[0-2]|0?[1-9])(?P=sep)(?P<d>3[01]|[12]\d|0?[1-9])(?!\d)"
    r"(?:(?:T|_| )(?P<H>[01]\d|2[0-3]):?(?P<Mi>[0-5]\d)(?::?(?P<S>[0-5]\d))?(?![\d:]))?"
)
RX_COMPACT = re.compile(
    r"(?<![\dA-Za-z])(?P<y>20\d{2})(?P<m>0[1-9]|1[0-2])(?P<d>0[1-9]|[12]\d|3[01])"
    r"(?:[T_ -]?(?P<H>[01]\d|2[0-3])(?P<Mi>[0-5]\d)(?P<S>[0-5]\d)?)?(?!\d)"
)
RX_NUMERIC_Y = re.compile(
    r"(?<![A-Za-z0-9./-])(?P<a>\d{1,2})(?P<sep>[/._-])(?P<b>\d{1,2})(?P=sep)(?P<y>(?:19|20)\d{2}|\d{2})(?![\d])"
    r"(?:\s*(?:-|–|—|to)\s*(?P<a2>\d{1,2})(?P=sep)(?P<b2>\d{1,2})(?P=sep)(?:(?:19|20)\d{2}|\d{2})(?!\d))?"
)
RX_NUMERIC_NOY = re.compile(
    r"(?<![A-Za-z0-9./-])(?P<a>1[0-2]|0?[1-9])(?P<sep>[/-])(?P<b>3[01]|[12]\d|0?[1-9])(?![\d/.]|-\d)"
)
RX_MON_DAY = re.compile(
    _WD + _MON.format(n="mon") + r"[\s_-]{0,2}" + _DAY.format(n="d")
    + r"(?:" + _DASH + r"(?:" + _MON.format(n="mon2") + r"[\s_-]{0,2})?" + _DAY.format(n="d2") + r")?"
    + r"(?:,?\s*(?:'|’)?" + _YEAR + r")?",
    re.I,
)
RX_DAY_MON = re.compile(
    _WD + _DAY.format(n="d") + r"(?:\s*(?:-|–|—)\s*" + _DAY.format(n="d2") + r")?" + r"[\s._-]*(?:of\s+)?"
    + _MON.format(n="mon") + r"(?:[\s,._-]*" + _YEAR + r")?",
    re.I,
)
RX_TIME_12 = re.compile(r"(?<![\d:])(?P<h>1[0-2]|0?[1-9])(?::(?P<m>[0-5]\d))?\s*(?P<ap>[ap])\.?\s?m\b\.?", re.I)
RX_TIME_24 = re.compile(r"(?<![\d:])(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?![\d:])")

LABEL_PRIMARY = re.compile(
    r"\b(?:(?:meeting|recording|session|call|workshop|event|interview|review|demo|class|visit|trip|conference)\s+date"
    r"|date\s+of\s+(?:the\s+)?(?:meeting|call|session|recording|event|workshop)"
    r"|^\W*date\b|\|\s*date\s*\||\bheld\s+on|\brecorded\s+on|\bwhen\s*[:|])",
    re.I,
)
LABEL_DEADLINE = re.compile(
    r"\b(?:due|deadline|by|until|before|no later than|target(?:ed)?|go[- ]live|cut-?over|launch(?:es|ing)?|"
    r"expir(?:es|y|ation)|end date|ends?|eta|delivery|deliver(?:ed)? by|submit(?:ted)? by)\b[^.\n]{0,24}$",
    re.I,
)
DATEISH_BEFORE = re.compile(r"\b(?:on|by|due|until|before|after|from|starting|since|week of|date|dated|deadline|"
                            r"(?:mon|tues|wednes|thurs|fri|satur|sun)day)\W{0,3}$", re.I)

SOURCE_LABELS = {
    "manual": "You set this",
    "you": "You set this",
    "claude": "Set by Claude",
    "ai": "Picked by your local AI",
    "frontmatter": "Date in the file's front matter",
    "filename": "Date in the file name",
    "title": "Date in the title",
    "content-label": "Labelled date in the document",
    "content-head": "Date at the top of the document",
    "body": "Date repeated through the document",
    "email": "Email sent date",
    "metadata": "Document properties",
    "git": "When the file was added to the repo",
    "mtime": "When the file was last changed",
    "new-file": "When the file appeared",
    "created": "When you created the note",
    "daily": "Daily note",
    "none": "Marked as having no date",
}


# ---------------------------------------------------------------------------
# Finding dates in text
# ---------------------------------------------------------------------------
@dataclass
class Found:
    start: date
    end: date | None
    time: str | None
    has_year: bool
    weekday_ok: bool | None
    pos: int
    stop: int
    text: str
    kind: str


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _mon(s: str | None) -> int | None:
    return MONTHS.get((s or "")[:3].lower()) if s else None


def _wd(s: str | None) -> int | None:
    return WEEKDAYS.get((s or "")[:3].lower()) if s else None


def _hm(h: int, m: int) -> str:
    return f"{h:02d}:{m:02d}"


def infer_year(month: int, day: int, ref: date, weekday: int | None = None) -> tuple[date | None, bool | None]:
    """Pick the year that puts month/day nearest to `ref`.

    A weekday ("Tue Nov 17") only breaks near-ties: documents sometimes get the
    weekday wrong, and that must not push a date a year away.
    """
    options = [d for y in range(ref.year - 2, ref.year + 3) if (d := _valid(y, month, day))]
    if not options:
        return None, None
    options.sort(key=lambda d: abs((d - ref).days))
    best = options[0]
    if weekday is not None and best.weekday() != weekday:
        near = [d for d in options[1:] if d.weekday() == weekday and abs((d - ref).days) <= abs((best - ref).days) + 45]
        if near:
            best = near[0]
    if abs((best - ref).days) > 200:
        return None, None
    return best, (None if weekday is None else best.weekday() == weekday)


def _time_near(text: str, start: int, window: int = 70) -> str | None:
    seg = text[start: start + window].split("\n", 1)[0]
    m = RX_TIME_12.search(seg)
    if m:
        h = int(m.group("h")) % 12 + (12 if m.group("ap").lower() == "p" else 0)
        return _hm(h, int(m.group("m") or 0))
    m = RX_TIME_24.search(seg)
    if m:
        return _hm(int(m.group("h")), int(m.group("m")))
    return None


def find_dates(text: str, ref: date | None = None, *, filename: bool = False, allow_partial: bool = True,
               limit: int = 400) -> list[Found]:
    """Every calendar date in `text`, left to right, without overlaps.

    `filename=True` also accepts compact forms (20261015, 2026-10-23_143032) that
    are too ambiguous inside running text. Dates without a year are resolved
    against `ref` (or dropped when there's no reference).
    """
    if not text:
        return []
    ref = ref or date.today()
    out: list[Found] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= x or a >= y for x, y in taken)

    def add(f: Found) -> None:
        out.append(f)
        taken.append((f.pos, f.stop))

    # 1. ISO-style: 2026-10-15, 2026_10_15, 2026/10/15 (+ optional time)
    for m in RX_ISO.finditer(text):
        d = _valid(int(m["y"]), int(m["m"]), int(m["d"]))
        if not d or not free(m.start(), m.end()):
            continue
        t = None
        if m["H"] is not None and (m["S"] is not None or ":" in m.group(0)[10:] or filename):
            t = _hm(int(m["H"]), int(m["Mi"]))
        elif m["H"] is not None:  # "2026-10-15 2026" style — not a time; keep the date only
            add(Found(d, None, None, True, None, m.start(), m.start() + 10, m.group(0)[:10], "iso"))
            continue
        add(Found(d, None, t, True, None, m.start(), m.end(), m.group(0), "iso"))

    # 2. Compact 20261015 (file names only — too risky in prose: ids, amounts)
    if filename:
        for m in RX_COMPACT.finditer(text):
            d = _valid(int(m["y"]), int(m["m"]), int(m["d"]))
            if not d or not free(m.start(), m.end()):
                continue
            t = _hm(int(m["H"]), int(m["Mi"])) if m["H"] else None
            add(Found(d, None, t, True, None, m.start(), m.end(), m.group(0), "compact"))

    # 3. Month names: "Friday, October 9, 2026", "Oct 15th", "Nov17-18", "15 October 2026"
    for rx, kind in ((RX_MON_DAY, "month"), (RX_DAY_MON, "dmy")):
        for m in rx.finditer(text):
            if not free(m.start(), m.end()):
                continue
            if len(m["mon"]) > 4 and text[m.end("mon"): m.end("mon") + 1] == ".":
                continue  # "…mid-February. 4 Bring…" is a sentence break, not Feb 4
            mon = _mon(m["mon"])
            day = int(m["d"])
            wd = _wd(m["wd"])
            has_year = bool(m["y"])
            if has_year:
                d = _valid(int(m["y"]), mon, day)
                wd_ok = None if wd is None or d is None else d.weekday() == wd
            elif allow_partial:
                d, wd_ok = infer_year(mon, day, ref, wd)
            else:
                continue
            if not d:
                continue
            end = None
            if m["d2"]:
                mon2 = _mon(m.groupdict().get("mon2")) or mon
                y2 = d.year + (1 if mon2 < d.month else 0)
                end = _valid(y2, mon2, int(m["d2"]))
                if end and not (d < end <= d + timedelta(days=31)):
                    end = None
            add(Found(d, end, _time_near(text, m.end()), has_year, wd_ok, m.start(), m.end(), m.group(0), kind))

    # 4. Numeric with a year: 10/15/2026, 10-15-26, 15.10.2026 (month first unless impossible)
    for m in RX_NUMERIC_Y.finditer(text):
        if not free(m.start(), m.end()):
            continue
        a, b = int(m["a"]), int(m["b"])
        y = int(m["y"])
        if y < 100:
            y += 2000
        if m["sep"] == "." and not filename and len(m["y"]) == 2:
            continue  # "1.2.26" in prose is more likely a version number
        if m["sep"] == "_" and not filename:
            continue
        if a <= 12:
            d = _valid(y, a, b)
        else:
            d = _valid(y, b, a)
        if not d:
            continue
        end = None
        if m["a2"]:
            a2, b2 = int(m["a2"]), int(m["b2"])
            end = _valid(d.year, a2, b2) if a <= 12 else _valid(d.year, b2, a2)
            if end and not (d < end <= d + timedelta(days=31)):
                end = None
        add(Found(d, end, _time_near(text, m.end()), True, None, m.start(), m.end(), m.group(0), "us"))

    # 5. Numeric without a year: 10/15 (prose needs a cue like "on", "by", "due")
    if allow_partial:
        for m in RX_NUMERIC_NOY.finditer(text):
            if not free(m.start(), m.end()):
                continue
            if m["sep"] == "-" and not filename:
                continue
            if not filename and not DATEISH_BEFORE.search(text[max(0, m.start() - 16): m.start()]):
                continue
            if text[max(0, m.start() - 1): m.start()] in ("v", "V", "#", "$"):
                continue
            d, _ = infer_year(int(m["a"]), int(m["b"]), ref)
            if not d:
                continue
            add(Found(d, None, _time_near(text, m.end()), False, None, m.start(), m.end(), m.group(0), "us"))
        if len(out) >= limit:
            pass
    out.sort(key=lambda f: f.pos)
    return out[:limit]


# ---------------------------------------------------------------------------
# Titles from file names
# ---------------------------------------------------------------------------
def clean_title(stem: str, ref: date | None = None, *, kind: str = "") -> str:
    """'Data_Plane_Intro_Meeting_Notes_2026-10-05' -> 'Data Plane Intro Meeting Notes'.

    The date comes out of the title because Daybook shows it on its own.
    A name that is only a timestamp ('2026-10-23_143032_000') becomes
    'Recording 2:30 PM' for transcripts, or keeps the timestamp otherwise.
    """
    found = find_dates(stem, ref, filename=True)
    s = stem
    time_label = None
    for f in sorted(found, key=lambda x: -x.pos):
        tail = s[f.stop:]
        counter = re.match(r"[_ -]\d{1,4}(?=$|[_ .-])", tail)  # recorder counters: _000
        stop = f.stop + (counter.end() if counter and f.time else 0)
        s = s[: f.pos] + " " + s[stop:]
        if f.time and not time_label:
            h, mi = (int(x) for x in f.time.split(":"))
            time_label = f"{(h % 12) or 12}:{mi:02d} {'AM' if h < 12 else 'PM'}"
    s = re.sub(r"[_]+", " ", s)
    s = re.sub(r"\s*[-–—,.]+\s*$", "", s.strip())
    s = re.sub(r"^\s*[-–—,.]+\s*", "", s)
    s = re.sub(r"\s{2,}", " ", s).strip()
    if not s or not re.search(r"[A-Za-z]", s):
        if kind == "transcript":
            return f"Recording {time_label}" if time_label else "Recording"
        return re.sub(r"[_]+", " ", stem).strip() or stem
    return s


# ---------------------------------------------------------------------------
# Lightweight text + metadata extraction (no third-party packages)
# ---------------------------------------------------------------------------
_TAG = re.compile(r"<[^>]+>")


def _xml_text(xml: str, para_tag: str) -> str:
    xml = re.sub(r"<w:tab/>|<a:tab/>", "\t", xml)
    xml = re.sub(r"</w:tc>", " | ", xml)
    xml = re.sub(rf"</{para_tag}>", "\n", xml)
    xml = re.sub(r"<w:br/>|<a:br/>", "\n", xml)
    return _html.unescape(_TAG.sub("", xml))


def _ooxml_core(z: zipfile.ZipFile) -> dict:
    try:
        x = z.read("docProps/core.xml").decode("utf-8", "ignore")
    except KeyError:
        return {}
    out = {}
    for key, tag in (("created", "dcterms:created"), ("modified", "dcterms:modified"), ("title", "dc:title"),
                     ("creator", "dc:creator")):
        m = re.search(rf"<{tag}[^>]*>([^<]*)</{tag}>", x)
        if m and m.group(1).strip():
            out[key] = _html.unescape(m.group(1).strip())
    return out


def quick_text(path: Path, max_chars: int = 400_000) -> tuple[str, dict]:
    """Plain text and metadata for date finding, using only the standard library."""
    ext = path.suffix.lower()
    meta: dict = {}
    text = ""
    try:
        if ext in (".docx", ".docm", ".dotx"):
            with zipfile.ZipFile(path) as z:
                meta = _ooxml_core(z)
                text = _xml_text(z.read("word/document.xml").decode("utf-8", "ignore"), "w:p")
        elif ext in (".pptx", ".pptm"):
            with zipfile.ZipFile(path) as z:
                meta = _ooxml_core(z)
                slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                                key=lambda n: int(re.findall(r"\d+", n)[-1]))
                meta["slides"] = len(slides)
                parts = []
                for i, n in enumerate(slides, 1):
                    parts.append(f"## Slide {i}\n" + _xml_text(z.read(n).decode("utf-8", "ignore"), "a:p"))
                # Speaker notes often say what the deck is for ("For discussion with Jacqueline, Tuesday October 20").
                notes = sorted((n for n in z.namelist() if re.match(r"ppt/notesSlides/notesSlide\d+\.xml$", n)),
                               key=lambda n: int(re.findall(r"\d+", n)[-1]))
                for n in notes:
                    note_text = _xml_text(z.read(n).decode("utf-8", "ignore"), "a:p").strip()
                    if note_text:
                        parts.append("Speaker notes: " + note_text)
                text = "\n".join(parts)
        elif ext in (".xlsx", ".xlsm"):
            with zipfile.ZipFile(path) as z:
                meta = _ooxml_core(z)
                try:
                    text = _xml_text(z.read("xl/sharedStrings.xml").decode("utf-8", "ignore"), "si")
                except KeyError:
                    text = ""
        elif ext == ".pdf":
            try:
                from pypdf import PdfReader  # optional
                r = PdfReader(str(path))
                info = r.metadata or {}
                for key, k in (("/CreationDate", "created"), ("/ModDate", "modified"), ("/Title", "title")):
                    v = info.get(key)
                    if v:
                        meta[k] = str(v)
                text = "\n".join((p.extract_text() or "") for p in r.pages[:4])
            except Exception:
                text = ""
        elif ext in (".eml",):
            raw = path.read_bytes()[:max_chars].decode("utf-8", "ignore")
            m = re.search(r"^Date:\s*(.+)$", raw, re.M)
            if m:
                meta["email_date"] = m.group(1).strip()
            text = raw
        elif ext in (".html", ".htm", ".xhtml"):
            raw = path.read_bytes()[:max_chars].decode("utf-8", "ignore")
            m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I | re.S)
            if m:
                meta["title"] = _html.unescape(re.sub(r"\s+", " ", m.group(1))).strip()
            raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
            text = _html.unescape(_TAG.sub(" ", raw))
        else:
            raw = path.read_bytes()[:max_chars]
            for enc in ("utf-8", "utf-16", "cp1252"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
    except (OSError, zipfile.BadZipFile, KeyError, ValueError):
        pass
    text = re.sub(r"[ \t]+", " ", text)
    return text[:max_chars], meta


def parse_timestamp(value: str | None) -> datetime | None:
    """ISO 8601 / PDF 'D:20261015...' / RFC 2822 (emails) -> aware datetime."""
    if not value:
        return None
    v = value.strip()
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?(Z|[+-]\d{2}'?\d{2}'?)?", v)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        h, mi, s = int(m[4] or 0), int(m[5] or 0), int(m[6] or 0)
        tz = timezone.utc
        if m[7] and m[7] != "Z":
            sign = 1 if m[7][0] == "+" else -1
            digits = re.sub(r"\D", "", m[7])
            tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:4] or 0)))
        try:
            return datetime(y, mo, d, h, mi, s, tzinfo=tz)
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(v)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        return None


def local_day(dt: datetime | float | int | None, tz_name: str | None) -> tuple[date, str] | None:
    """A timestamp (aware datetime or epoch seconds) as the local (date, 'HH:MM')."""
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        dt = datetime.fromtimestamp(dt, tz=timezone.utc)
    tz = None
    if tz_name and ZoneInfo is not None:
        try:
            tz = ZoneInfo(tz_name)
        except Exception:
            tz = None
    local = dt.astimezone(tz) if tz else dt.astimezone()
    return local.date(), local.strftime("%H:%M")


# ---------------------------------------------------------------------------
# Git and file-time helpers
# ---------------------------------------------------------------------------
def git_added_dates(root: Path, timeout: float = 20) -> dict[str, datetime]:
    """Path -> when it was first added to the repository (read-only; never takes git locks)."""
    if not (root / ".git").exists():
        return {}
    try:
        out = subprocess.run(
            ["git", "-c", "core.quotepath=off", "--no-optional-locks", "log", "--diff-filter=A", "--name-only",
             "--format=\x1e%aI", "--no-renames"],
            cwd=str(root), capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if out.returncode != 0:
        return {}
    added: dict[str, datetime] = {}
    for block in out.stdout.split("\x1e"):
        lines = [l for l in block.strip().splitlines() if l.strip()]
        if not lines:
            continue
        when = parse_timestamp(lines[0])
        if not when:
            continue
        for name in lines[1:]:
            name = name.strip().strip('"')
            prev = added.get(name)
            if prev is None or when < prev:  # log is newest-first; keep the earliest add
                added[name] = when
    return added


def clustered_mtimes(mtimes: dict[str, float], window: float = 120, min_files: int = 4) -> set[str]:
    """Paths whose modified time is shared with many others (a git pull or a copy), so it says nothing."""
    items = sorted(mtimes.items(), key=lambda kv: kv[1])
    out: set[str] = set()
    i = 0
    for j in range(len(items)):
        while items[j][1] - items[i][1] > window:
            i += 1
        if j - i + 1 >= min_files:
            out.update(p for p, _ in items[i: j + 1])
    return out


def template_fingerprints(metas: dict[str, dict]) -> set[str]:
    """Created-timestamps shared by several files come from a template, not from the document."""
    seen: dict[str, int] = {}
    for m in metas.values():
        c = (m or {}).get("created")
        if c:
            seen[c] = seen.get(c, 0) + 1
    return {c for c, n in seen.items() if n >= 2}


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------
@dataclass
class Candidate:
    date: str
    source: str
    score: float
    evidence: str = ""
    end: str | None = None
    time: str | None = None


@dataclass
class Decision:
    date: str | None
    end: str | None
    time: str | None
    source: str | None
    confidence: float
    status: str
    evidence: str
    candidates: list[dict] = field(default_factory=list)
    mentions: list[dict] = field(default_factory=list)
    conflict: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _snippet(text: str, a: int, b: int, pad: int = 70) -> str:
    s = text[max(0, a - pad): b + pad]
    s = re.sub(r"[*_`#>|]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return ("…" if a > pad else "") + s + ("…" if b + pad < len(text) else "")


def content_candidates(text: str, ref: date, *, head_lines: int = 8, label_lines: int = 60) -> list[Candidate]:
    """Labelled dates ('Meeting date | Friday, October 9, 2026') and dates in the opening lines."""
    out: list[Candidate] = []
    if not text:
        return out
    lines = text.splitlines()
    nonempty = 0
    offset = 0
    for i, line in enumerate(lines[:400]):
        stripped = line.strip()
        plain = re.sub(r"[*_`]", "", stripped)
        if stripped:
            nonempty += 1
        if nonempty and nonempty <= label_lines and LABEL_PRIMARY.search(plain):
            for f in find_dates(plain, ref):
                if LABEL_DEADLINE.search(plain[: f.pos]):
                    continue
                score = 0.92 if f.has_year else 0.85
                if f.weekday_ok is False:
                    score *= 0.6
                out.append(Candidate(f.start.isoformat(), "content-label", score, _snippet(plain, f.pos, f.stop, 50),
                                     f.end.isoformat() if f.end else None, f.time or _time_near(plain, f.stop, 90)))
                break
        elif stripped and nonempty <= head_lines:
            for f in find_dates(plain, ref):
                if LABEL_DEADLINE.search(plain[: f.pos]):
                    continue
                score = 0.8 if f.has_year else 0.6
                if f.weekday_ok is False:
                    score *= 0.6
                out.append(Candidate(f.start.isoformat(), "content-head", score, _snippet(plain, f.pos, f.stop, 50),
                                     f.end.isoformat() if f.end else None, f.time))
                break
        offset += len(line) + 1
        if nonempty > label_lines:
            break
    return out


def body_candidate(text: str, ref: date) -> Candidate | None:
    found = [f for f in find_dates(text, ref, allow_partial=False) if f.has_year]
    if len(found) < 3:
        return None
    counts: dict[date, int] = {}
    for f in found:
        counts[f.start] = counts.get(f.start, 0) + 1
    best, n = max(counts.items(), key=lambda kv: kv[1])
    if n >= 3 and n / len(found) >= 0.5:
        return Candidate(best.isoformat(), "body", 0.6, f"Mentioned {n} times")
    return None


def mentions(text: str, ref: date, primary: str | None = None, limit: int = 40) -> list[dict]:
    """Other dates the document talks about (deadlines, events), with the surrounding words."""
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for f in find_dates(text, ref):
        iso = f.start.isoformat()
        if iso == primary and not f.end:
            continue
        before = text[max(0, f.pos - 40): f.pos]
        kind = "deadline" if LABEL_DEADLINE.search(before) else "mention"
        snip = _snippet(text, f.pos, f.stop, 60)
        key = (iso, snip[:60])
        if key in seen:
            continue
        seen.add(key)
        out.append({"date": iso, "end": f.end.isoformat() if f.end else None, "kind": kind, "snippet": snip[:240]})
        if len(out) >= limit:
            break
    return out


ABOUT = {"frontmatter", "filename", "title", "content-label", "content-head", "email", "body"}
"""Clues about what the document is *about* (the meeting, the event). The rest (document properties, git,
file times, creation) say when the file was *made*, which is often days later, so they only decide when
nothing in the name or text gives a date, and they never count as a disagreement."""


def _noisy_or(cs: list[Candidate], only: set | None = None) -> float:
    miss = 1.0
    for c in cs:
        if only is None or c.source in only:
            miss *= 1 - max(0.0, min(c.score, 0.999))
    return 1 - miss


def combine(cands: list[Candidate]) -> Decision:
    """Group clues by day; agreeing clues reinforce each other (noisy-OR)."""
    if not cands:
        return Decision(None, None, None, None, 0.0, "none", "", [], [])
    groups: dict[str, list[Candidate]] = {}
    for c in cands:
        groups.setdefault(c.date, []).append(c)
    rows = [{"date": d, "all": _noisy_or(cs), "about": _noisy_or(cs, ABOUT), "cs": cs} for d, cs in groups.items()]
    about = sorted((r for r in rows if r["about"] > 0), key=lambda r: -r["about"])
    if about and about[0]["about"] >= 0.55:
        pick = about[0]
        conflict = len(about) > 1 and about[1]["about"] >= SET_AT and pick["about"] - about[1]["about"] < 0.2
    else:
        pick = max(rows, key=lambda r: r["all"])
        conflict = False
    conf, d, cs = pick["all"], pick["date"], pick["cs"]
    ranked = sorted(cs, key=lambda c: (c.source not in ABOUT, -c.score))
    best = ranked[0]
    end = next((c.end for c in ranked if c.end), None)
    tm = next((c.time for c in ranked if c.time), None)
    status = "set" if conf >= SET_AT else "suggested" if conf >= SUGGEST_AT else "none"
    if status == "set" and conflict:
        status = "review"
    ev = best.evidence or SOURCE_LABELS.get(best.source, best.source)
    return Decision(
        date=d if status != "none" else None,
        end=end if status != "none" else None,
        time=tm if status != "none" else None,
        source=best.source if status != "none" else None,
        confidence=round(conf, 3),
        status=status,
        evidence=ev,
        candidates=[asdict(c) for c in sorted(cands, key=lambda c: (c.date != d, c.source not in ABOUT, -c.score))],
        conflict=conflict,
    )


UNDATED_NAMES = {"readme", "license", "licence", "changelog", "contributing", "notice", "code_of_conduct", "security"}


def decide(*, name: str, text: str = "", meta: dict | None = None, title: str | None = None,
           frontmatter: dict | None = None, git_added: datetime | None = None, mtime: float | None = None,
           mtime_trusted: bool = True, new_file: bool = False, created_ms: int | None = None,
           templates: set[str] | None = None, tz_name: str | None = None, sidecar: dict | None = None,
           today: date | None = None, want_mentions: bool = True, kind: str = "") -> Decision:
    """Every clue for one document, combined into a decision."""
    meta = meta or {}
    today = today or date.today()
    cands: list[Candidate] = []

    # What the file itself says about when it was made — also the reference for year-less dates.
    stamp_ref: date | None = None
    git_day = local_day(git_added, tz_name) if git_added else None
    mtime_day = local_day(mtime, tz_name) if mtime else None
    if git_day:
        stamp_ref = git_day[0]
    elif mtime_day:
        stamp_ref = mtime_day[0]
    ref = stamp_ref or today

    # Explicit decisions win outright.
    if not sidecar and Path(name).stem.lower() in UNDATED_NAMES:
        return Decision(None, None, None, "none", 1.0, "manual", "A reference file (README, LICENSE…), not tied to a day")
    if sidecar:
        if sidecar.get("none"):
            return Decision(None, None, None, "none", 1.0, "manual", sidecar.get("note") or "Marked as having no date")
        if sidecar.get("date"):
            src = sidecar.get("by") or "manual"
            return Decision(sidecar["date"], sidecar.get("end"), sidecar.get("time"), src, 1.0, "manual",
                            sidecar.get("note") or SOURCE_LABELS.get(src, "Set by hand"))

    fm = frontmatter or {}
    for key in ("date", "day", "meeting_date", "created"):
        v = fm.get(key)
        if v:
            found = find_dates(str(v), ref, filename=True)
            if found:
                f = found[0]
                cands.append(Candidate(f.start.isoformat(), "frontmatter", 0.99 if key != "created" else 0.8,
                                       f"{key}: {v}", f.end.isoformat() if f.end else None, f.time))
                break

    stem = Path(name).stem
    for f in find_dates(stem, ref, filename=True)[:2]:
        score = 0.95 if f.has_year else 0.8
        if f.weekday_ok is False:
            score *= 0.6
        cands.append(Candidate(f.start.isoformat(), "filename", score, Path(name).name,
                               f.end.isoformat() if f.end else None, f.time))
        break

    for t in (title, meta.get("title")):
        if t and t != stem:
            fs = find_dates(t, ref)
            if fs:
                f = fs[0]
                cands.append(Candidate(f.start.isoformat(), "title", 0.88 if f.has_year else 0.75, t[:120],
                                       f.end.isoformat() if f.end else None, f.time))
                break

    if meta.get("email_date"):
        dt = parse_timestamp(meta["email_date"])
        ld = local_day(dt, tz_name) if dt else None
        if ld:
            cands.append(Candidate(ld[0].isoformat(), "email", 0.95, f"Sent {meta['email_date']}", None, ld[1]))

    if kind != "transcript":
        cands += content_candidates(text, ref)
    bc = body_candidate(text, ref) if kind != "transcript" else None
    if bc:
        cands.append(bc)

    # File properties, unless they came from a template or are wildly older than the file.
    templates = templates or set()
    anchor = stamp_ref or today
    for key, score in (("created", 0.45), ("modified", 0.35)):
        raw = meta.get(key)
        if not raw or raw in templates or (key == "modified" and meta.get("created") in templates):
            continue
        dt = parse_timestamp(raw)
        ld = local_day(dt, tz_name) if dt else None
        if not ld or abs((ld[0] - anchor).days) > 120 or ld[0] > today + timedelta(days=1):
            continue
        cands.append(Candidate(ld[0].isoformat(), "metadata", score, f"Document {key} {ld[0].isoformat()}"))

    if git_day:
        cands.append(Candidate(git_day[0].isoformat(), "git", 0.5, f"Added to the repo {git_day[0].isoformat()} {git_day[1]}"))
    elif mtime_day and mtime_trusted:
        if new_file:
            cands.append(Candidate(mtime_day[0].isoformat(), "new-file", 0.75, f"Appeared {mtime_day[0].isoformat()} {mtime_day[1]}"))
        else:
            cands.append(Candidate(mtime_day[0].isoformat(), "mtime", 0.35, f"Last changed {mtime_day[0].isoformat()} {mtime_day[1]}"))
    if created_ms:
        ld = local_day(created_ms / 1000, tz_name)
        if ld:
            cands.append(Candidate(ld[0].isoformat(), "created", 0.75, f"Created {ld[0].isoformat()}"))

    dec = combine(cands)
    if want_mentions and text:
        dec.mentions = mentions(text, date.fromisoformat(dec.date) if dec.date else ref, dec.date)
    return dec


# ---------------------------------------------------------------------------
# The sidecar: <library>/.daybook/dates.json
# ---------------------------------------------------------------------------
SIDECAR_ABOUT = (
    "Dates assigned to files in this folder. Daybook reads this file and puts each file on that day of the "
    "calendar. Entries are added when you (or your local AI, or Claude) pick a date. Edit by hand or run "
    "Daybook/daybook-dates. Keys are paths relative to this folder."
)


def sidecar_path(root: Path) -> Path:
    return Path(root) / SIDECAR_DIR / SIDECAR_FILE


def load_sidecar(root: Path) -> dict:
    p = sidecar_path(root)
    try:
        data = json.loads(p.read_text("utf-8"))
        if isinstance(data, dict) and isinstance(data.get("dates"), dict):
            return data
    except (OSError, ValueError):
        pass
    return {"version": 1, "about": SIDECAR_ABOUT, "dates": {}}


def save_sidecar(root: Path, data: dict) -> None:
    p = sidecar_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"version": 1, "about": SIDECAR_ABOUT, "dates": dict(sorted((data.get("dates") or {}).items()))}
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", "utf-8")
    os.replace(tmp, p)


def set_entry(root: Path, rel: str, entry: dict | None) -> dict:
    data = load_sidecar(root)
    if entry is None:
        data["dates"].pop(rel, None)
    else:
        clean = {k: v for k, v in entry.items() if v not in (None, "", False)}
        clean.setdefault("at", date.today().isoformat())
        data["dates"][rel] = clean
    save_sidecar(root, data)
    return data


def rename_entry(root: Path, old: str, new: str) -> None:
    data = load_sidecar(root)
    if old in data["dates"]:
        data["dates"][new] = data["dates"].pop(old)
        save_sidecar(root, data)


def load_config(root: Path) -> dict:
    try:
        return json.loads((Path(root) / SIDECAR_DIR / CONFIG_FILE).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def is_library(root: Path) -> bool:
    return (Path(root) / SIDECAR_DIR / CONFIG_FILE).exists()


# ---------------------------------------------------------------------------
# Walking a library without Daybook running (used by the daybook-dates command)
# ---------------------------------------------------------------------------
LIBRARY_EXTS = {
    ".md", ".markdown", ".mdown", ".txt", ".text", ".log", ".docx", ".docm", ".dotx", ".doc", ".rtf", ".odt", ".pdf",
    ".pptx", ".pptm", ".xlsx", ".xlsm", ".csv", ".tsv", ".html", ".htm", ".xhtml", ".eml", ".msg", ".png", ".jpg",
    ".jpeg", ".gif", ".webp", ".heic", ".tif", ".tiff",
}
_SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", "dist", "build", "target"}


def iter_library_files(root: Path):
    """(relative path, absolute path, stat) for every file Daybook would read."""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        base = Path(dirpath)
        keep = []
        for d in dirnames:
            full = base / d
            if d.startswith(".") or d in _SKIP_DIRS or full.is_symlink():
                continue
            if (full / "run.py").is_file() and (full / "app" / "main.py").is_file():
                continue  # Daybook's own code
            keep.append(d)
        dirnames[:] = sorted(keep)
        for f in sorted(filenames):
            if f.startswith((".", "~$", "._")) or Path(f).suffix.lower() not in LIBRARY_EXTS:
                continue
            p = base / f
            try:
                st = p.lstat()
            except OSError:
                continue
            if p.is_symlink() or not p.is_file():
                continue
            yield p.relative_to(root).as_posix(), p, st


def scan_library(root: Path, *, tz_name: str | None = None, use_git: bool = True, today: date | None = None) -> list[dict]:
    """Decide a date for every file in a library folder (no database, no Daybook server)."""
    root = Path(root)
    cfg = load_config(root)
    tz_name = tz_name or cfg.get("timezone")
    sidecar = load_sidecar(root)["dates"]
    git = git_added_dates(root) if use_git else {}
    files = list(iter_library_files(root))
    texts, metas = {}, {}
    for rel, p, st in files:
        texts[rel], metas[rel] = quick_text(p)
    clustered = clustered_mtimes({rel: st.st_mtime for rel, _p, st in files})
    templates = template_fingerprints(metas)
    out = []
    for rel, p, st in files:
        stem = p.stem
        kind = "transcript" if p.suffix.lower() in (".txt", ".text", ".log") and (
            re.search(r"transcript|recording", stem, re.I) or any(f.time for f in find_dates(stem, filename=True))) else ""
        dec = decide(name=p.name, text=texts[rel], meta=metas[rel], git_added=git.get(rel), mtime=st.st_mtime,
                     mtime_trusted=rel not in clustered, templates=templates, tz_name=tz_name, sidecar=sidecar.get(rel),
                     today=today, kind=kind)
        out.append({"path": rel, **dec.as_dict()})
    return out
