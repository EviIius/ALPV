"""Small shared helpers: ids, time, and Markdown text utilities."""
from __future__ import annotations

import json
import re
import secrets
import string
import time
import unicodedata
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_ALPHABET = string.ascii_letters + string.digits


def new_id(n: int = 12) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def now_ms() -> int:
    return int(time.time() * 1000)


def tzinfo(name: str | None = None) -> ZoneInfo:
    if name is None:
        from . import db

        name = db.get_settings().get("timezone") or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_now(tz_name: str | None = None) -> datetime:
    return datetime.now(tzinfo(tz_name))


def today_str(tz_name: str | None = None) -> str:
    return local_now(tz_name).date().isoformat()


def parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def jloads(s: str | None, default):
    if not s:
        return default
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------
_RE_FENCE = re.compile(r"^\s*```[^\n]*$", re.M)
_RE_IMG = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_RE_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_RE_TAG = re.compile(r"<[^>\n]+>")
_RE_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_RE_TASK = re.compile(r"^(\s*)[-*+]\s+\[[ xX]\]\s+", re.M)
_RE_LIST = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+", re.M)
_RE_QUOTE = re.compile(r"^\s*>\s?", re.M)
_RE_TABLE_SEP = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-*:?\s*$", re.M)
_RE_EMPH = [
    re.compile(r"\*\*(.+?)\*\*", re.S),
    re.compile(r"__(.+?)__", re.S),
    re.compile(r"~~(.+?)~~", re.S),
    re.compile(r"==(.+?)==", re.S),
    re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])"),
    re.compile(r"(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])"),
    re.compile(r"`([^`\n]+)`"),
]
_RE_NOTE_LINK = re.compile(r"\]\(#/notes/([A-Za-z0-9]{4,40})\)")


def md_to_text(md: str | None) -> str:
    s = md or ""
    s = _RE_FENCE.sub("", s)
    s = _RE_IMG.sub(r"\1", s)
    s = _RE_LINK.sub(r"\1", s)
    s = _RE_TAG.sub("", s)
    s = _RE_HEADING.sub("", s)
    s = _RE_TASK.sub(r"\1", s)
    s = _RE_LIST.sub(r"\1", s)
    s = _RE_QUOTE.sub("", s)
    s = _RE_TABLE_SEP.sub("", s)
    for rx in _RE_EMPH:
        s = rx.sub(r"\1", s)
    s = s.replace("|", " ").replace("&nbsp;", " ").replace(" ", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", s)
    return s.strip()


def word_count(md: str | None) -> int:
    return len(re.findall(r"[\w'’-]+", md_to_text(md)))


def snippet(md: str | None, n: int = 180) -> str:
    text = re.sub(r"\s+", " ", md_to_text(md)).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def note_links(md: str | None) -> list[str]:
    return sorted(set(_RE_NOTE_LINK.findall(md or "")))


def first_heading(md: str | None) -> str | None:
    m = re.search(r"^\s{0,3}#{1,3}\s+(.+?)\s*#*\s*$", md or "", re.M)
    return m.group(1).strip() if m else None


def clean_tags(tags) -> list[str]:
    out: list[str] = []
    if isinstance(tags, str):
        tags = re.split(r"[,\s]+", tags)
    for t in tags or []:
        t = str(t).strip().lstrip("#").strip().lower()
        t = re.sub(r"[^\w\-/]+", "-", t).strip("-")
        if t and t not in out and len(t) <= 40:
            out.append(t)
    return out[:20]


def safe_filename(name: str, default: str = "file") -> str:
    name = unicodedata.normalize("NFKC", name or "").strip()
    name = re.sub(r"[\x00-\x1f/\\:*?\"<>|]+", "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return (name or default)[:120]


def title_from_filename(filename: str) -> str:
    stem = filename.rsplit("/", 1)[-1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    stem = re.sub(r"[_]+", " ", stem)
    stem = re.sub(r"\s+", " ", stem).strip()
    return stem[:1].upper() + stem[1:] if stem else "Imported document"
