"""Natural-language quick capture, done with rules (instant, no AI needed).

    "Lunch with Kelly tomorrow 12:30-1:30 @ Panera"   -> event
    "Pay rent on the 1st every month !high"           -> task (recurring hint)
    "Call the bank friday"                            -> task due Friday
    "Standup every weekday 9:15am for 15m #work"      -> recurring event

Local AI can re-interpret anything the rules miss (Quick add → "Ask local AI").
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

WEEKDAYS = {"mon": 0, "tue": 1, "tues": 1, "wed": 2, "weds": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
WD_RX = r"(mon|tue|tues|wed|weds|thu|thur|thurs|fri|sat|sun)(?:day|nesday|sday|urday|rsday)?"
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
MON_RX = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
RRULE_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
EVENT_WORDS = re.compile(
    r"\b(meeting|meet|appointment|appt|lunch|dinner|breakfast|brunch|coffee|drinks|interview|flight|party|"
    r"birthday|wedding|conference|sync|standup|stand-up|1:1|one-on-one|class|concert|doctor|dentist|"
    r"haircut|demo|workshop|webinar|presentation|trip|vacation|holiday)\b", re.I)
ALL_DAY_WORDS = re.compile(r"\b(birthday|anniversary|holiday|vacation|trip|day off|pto)\b", re.I)


def _hm(h: int, m: int, ampm: str | None) -> tuple[int, int] | None:
    ampm = (ampm or "").replace(".", "").lower()
    if ampm == "pm" and h < 12:
        h += 12
    elif ampm == "am" and h == 12:
        h = 0
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h, m


def _bare_hour(h: int) -> int:
    """'at 3' -> 15:00, 'at 9' -> 09:00 (business-hours guess)."""
    return h + 12 if 1 <= h <= 7 else h


def _fmt(t: tuple[int, int]) -> str:
    return f"{t[0]:02d}:{t[1]:02d}"


class _Text:
    def __init__(self, s: str) -> None:
        self.s = s

    def find(self, rx: str, flags=re.I):
        return re.search(rx, self.s, flags)

    def cut(self, m) -> None:
        self.s = " " + (self.s[: m.start()] + " " + self.s[m.end():]).strip() + " "

    def take(self, rx: str, flags=re.I):
        m = self.find(rx, flags)
        if m:
            self.cut(m)
        return m


def parse(text: str, now: datetime, default_minutes: int = 60) -> dict:
    raw = (text or "").strip()
    t = _Text(" " + raw + " ")
    today = now.date()
    out: dict = {"type": None, "title": "", "date": None, "start_time": None, "end_time": None, "all_day": False,
                 "location": "", "priority": "none", "tags": [], "rrule": None, "matched": []}

    m = t.take(r"^\s*(event|meeting|appt|appointment)\s*:\s*")
    if m:
        out["type"] = "event"
    m = m or t.take(r"^\s*(task|todo|to-do)\s*:\s*|^\s*remind me to\s+")
    if m and not out["type"]:
        out["type"] = "task"
    if not m:
        m = t.take(r"^\s*(note|idea)\s*:\s*")
        if m:
            out["type"] = "note"

    # Priority, tags, location -------------------------------------------------
    m = t.take(r"(?<!\S)!(high|hi|med|medium|low)\b")
    if m:
        out["priority"] = {"hi": "high", "med": "medium"}.get(m.group(1).lower(), m.group(1).lower())
    else:
        m = t.take(r"(?<!\S)(!{1,3})(?=\s|$)")
        if m:
            out["priority"] = ["low", "medium", "high"][len(m.group(1)) - 1]
    while True:
        m = t.take(r"(?<!\S)#([\w\-/]+)")
        if not m:
            break
        out["tags"].append(m.group(1).lower())
    m = t.take(r"(?<!\S)@\s*([^#!@]+?)(?=\s+(?:on|at|from|for|by|every|tomorrow|today|tonight)\b|\s+\d|$)")
    if m:
        out["location"] = m.group(1).strip(" ,.")

    # Recurrence -------------------------------------------------------------------
    m = t.take(r"\bevery\s+weekday\b|\bweekdays\b")
    if m:
        out["rrule"] = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    if not out["rrule"]:
        m = t.take(r"\bevery\s+(other\s+)?" + WD_RX + r"(?:\s*(?:,|and|&)\s*" + WD_RX + r")*\b")
        if m:
            days = re.findall(WD_RX, m.group(0), re.I)
            codes = sorted({RRULE_DAYS[WEEKDAYS[d.lower()]] for d in days}, key=RRULE_DAYS.index)
            out["rrule"] = f"FREQ=WEEKLY;{'INTERVAL=2;' if m.group(1) else ''}BYDAY={','.join(codes)}"
    if not out["rrule"]:
        m = t.take(r"\bevery\s+(\d+)\s+(day|week|month|year)s?\b")
        if m:
            out["rrule"] = f"FREQ={ {'day': 'DAILY', 'week': 'WEEKLY', 'month': 'MONTHLY', 'year': 'YEARLY'}[m.group(2).lower()] };INTERVAL={m.group(1)}".replace(" ", "")
    if not out["rrule"]:
        m = t.take(r"\b(every\s+(other\s+)?(day|week|month|year)|daily|weekly|monthly|yearly|annually|biweekly)\b")
        if m:
            word = m.group(0).lower()
            freq = "DAILY" if "day" in word or "daily" in word else "WEEKLY" if "week" in word else "MONTHLY" if "month" in word else "YEARLY"
            interval = ";INTERVAL=2" if ("other" in word or "biweekly" in word) else ""
            out["rrule"] = f"FREQ={freq}{interval}"

    # Duration ---------------------------------------------------------------------
    minutes = None
    m = t.take(r"\bfor\s+(\d+(?:\.\d+)?)\s*(h|hr|hrs|hour|hours|m|min|mins|minute|minutes)\b")
    if m:
        n = float(m.group(1))
        minutes = int(n * 60) if m.group(2).lower().startswith("h") else int(n)
    else:
        m = t.take(r"\bfor\s+(an?|half an?)\s+hour\b")
        if m:
            minutes = 30 if "half" in m.group(1).lower() else 60

    # Explicit dates (before times, so "12-20" in 2026-12-20 is not a time range)
    d: date | None = None
    if d is None:
        m = t.take(r"\b(\d{4})-(\d{2})-(\d{2})\b")
        if m:
            try:
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError:
                d = None
    if d is None:
        m = t.take(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")
        if m:
            year = int(m.group(3)) if m.group(3) else today.year
            year = year + 2000 if year < 100 else year
            try:
                d = date(year, int(m.group(1)), int(m.group(2)))
                if not m.group(3) and d < today - timedelta(days=1):
                    d = d.replace(year=year + 1)
            except ValueError:
                d = None
    if d is None:
        m = t.take(r"\b(?:on\s+)?" + MON_RX + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b")
        if m:
            mon = MONTHS[m.group(1).lower()[:3]]
            year = int(m.group(3)) if m.group(3) else today.year
            try:
                d = date(year, mon, int(m.group(2)))
                if not m.group(3) and d < today - timedelta(days=1):
                    d = d.replace(year=year + 1)
            except ValueError:
                d = None
    if d is None:
        m = t.take(r"\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)\s+(?:of\s+)?" + MON_RX + r"(?:,?\s+(\d{4}))?\b")
        if m:
            mon = MONTHS[m.group(2).lower()[:3]]
            year = int(m.group(3)) if m.group(3) else today.year
            try:
                d = date(year, mon, int(m.group(1)))
                if not m.group(3) and d < today - timedelta(days=1):
                    d = d.replace(year=year + 1)
            except ValueError:
                d = None
    # Times --------------------------------------------------------------------------
    start = end = None
    ap = r"(am|pm|a\.m\.|p\.m\.)"
    m = t.find(r"(?<![\d/-])\b(?:from\s+)?(\d{1,2})(?::(\d{2}))?\s*" + ap + r"?\s*(?:-|–|to|until|till)\s*(\d{1,2})(?::(\d{2}))?\s*" + ap + r"?(?=\s|$|[,.])")
    if m and (m.group(3) or m.group(6) or m.group(2) or m.group(5)):
        t.cut(m)
        h1, m1, a1, h2, m2, a2 = int(m.group(1)), int(m.group(2) or 0), m.group(3), int(m.group(4)), int(m.group(5) or 0), m.group(6)
        if a2 and not a1:
            a1 = a2 if (h1 <= h2 or h1 == 12) else ("am" if a2.lower().startswith("p") else "pm")
        if not a1 and not a2 and h1 <= 12 and h2 <= 12:
            h1, h2 = _bare_hour(h1), _bare_hour(h2)
        start, end = _hm(h1, m1, a1), _hm(h2, m2, a2)
    if start is None:
        m = t.take(r"\b(?:at\s+|@\s*)?(\d{1,2})(?::(\d{2}))?\s*" + ap + r"(?=\s|$|[,.])")
        if m:
            start = _hm(int(m.group(1)), int(m.group(2) or 0), m.group(3))
    if start is None:
        m = t.take(r"\b(?:at\s+)?([01]?\d|2[0-3]):([0-5]\d)\b")
        if m:
            start = (int(m.group(1)), int(m.group(2)))
    if start is None:
        m = t.take(r"\bat\s+(\d{1,2})\b(?!\s*(?:/|-|\.|st|nd|rd|th|%))")
        if m and 1 <= int(m.group(1)) <= 12:
            start = (_bare_hour(int(m.group(1))), 0)
    if start is None:
        m = t.take(r"\b(noon|midday|midnight)\b")
        if m:
            start = (0, 0) if m.group(1).lower() == "midnight" else (12, 0)

    # Relative dates -------------------------------------------------------------------
    if d is None:
        m = t.take(r"\bday after tomorrow\b")
        if m:
            d = today + timedelta(days=2)
    if d is None:
        m = t.take(r"\b(today|tonight|tomorrow|tmrw|tmr|tomorrow night)\b")
        if m:
            w = m.group(1).lower()
            d = today if w in ("today", "tonight") else today + timedelta(days=1)
            if "night" in w and start is None:
                start = (20, 0)
    if d is None:
        m = t.take(r"\bin\s+(\d+)\s+(day|week|month)s?\b")
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            d = today + (timedelta(days=n) if unit == "day" else timedelta(weeks=n) if unit == "week" else timedelta(days=30 * n))
    if d is None:
        m = t.take(r"\bin\s+(\d+)\s+(minute|min|hour|hr)s?\b")
        if m:
            n = int(m.group(1))
            at = now + (timedelta(hours=n) if m.group(2).lower().startswith("h") else timedelta(minutes=n))
            d, start = at.date(), (at.hour, at.minute)
    if d is None:
        m = t.take(r"\b(?:on\s+)?the\s+(\d{1,2})(?:st|nd|rd|th)\b")
        if m:
            day = int(m.group(1))
            try:
                d = today.replace(day=day)
                if d < today:
                    nm = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
                    d = nm.replace(day=day)
            except ValueError:
                d = None
    if d is None:
        m = t.take(r"\b(?:on\s+)?(next|this)?\s*" + WD_RX + r"\b")
        if m:
            target = WEEKDAYS[m.group(2).lower()]
            delta = (target - today.weekday()) % 7
            if delta == 0 and (m.group(1) or "").lower() != "this":
                delta = 7
            d = today + timedelta(days=delta)
    if d is None:
        m = t.take(r"\b(next week|this weekend|next weekend|next month)\b")
        if m:
            w = m.group(1).lower()
            if w == "next week":
                d = today + timedelta(days=7 - today.weekday())
            elif "weekend" in w:
                d = today + timedelta(days=(5 - today.weekday()) % 7 + (7 if w.startswith("next") else 0))
            else:
                d = (today.replace(day=1) + timedelta(days=32)).replace(day=1)

    # Part-of-day words ---------------------------------------------------------------
    if start is None:
        m = t.take(r"\b(?:in the\s+|this\s+)?(morning|afternoon|evening)\b")
        if m:
            start = {"morning": (9, 0), "afternoon": (14, 0), "evening": (18, 0)}[m.group(1).lower()]
    if t.take(r"\ball[\s-]day\b"):
        out["all_day"] = True

    # Title -----------------------------------------------------------------------------------
    title = re.sub(r"\s+", " ", t.s).strip(" ,.;-")
    for _ in range(3):
        title = re.sub(r"^(on|at|from|by|for|to|in|the)\s+", "", title, flags=re.I)
        title = re.sub(r"\s+(on|at|from|by|for|to|in|the|and|,)$", "", title, flags=re.I)
    title = title.strip(" ,.;-")
    out["title"] = (title[:1].upper() + title[1:]) if title else raw[:120]

    if start and not out["all_day"]:
        out["start_time"] = _fmt(start)
        if end is None:
            mins = minutes or default_minutes
            e = datetime.combine(d or today, datetime.min.time()).replace(hour=start[0], minute=start[1]) + timedelta(minutes=mins)
            end = (e.hour, e.minute)
        out["end_time"] = _fmt(end)
    if out["rrule"] and d is None:
        d = today

    if out["type"] is None:
        if start or out["rrule"] and EVENT_WORDS.search(raw):
            out["type"] = "event"
        elif d and (EVENT_WORDS.search(raw) or out["all_day"]):
            out["type"] = "event"
            if not start:
                out["all_day"] = True
        elif len(raw) > 140:
            out["type"] = "note"
        else:
            out["type"] = "task"
    if out["type"] == "event" and d is None:
        d = today
    if out["type"] == "event" and not start:
        out["all_day"] = True
    if out["type"] == "event" and ALL_DAY_WORDS.search(raw) and not start:
        out["all_day"] = True
    out["date"] = d.isoformat() if d else None
    return out
