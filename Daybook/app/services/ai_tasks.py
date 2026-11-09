"""Purpose-built local AI actions.

AI in Daybook is used for specific jobs at specific moments — filing an
imported document, pulling action items out of a meeting note, tidying a raw
transcript — and it always proposes; you decide. Quick jobs (titles, tags,
parsing) use the fast model; writing jobs use the smart model.
"""
from __future__ import annotations

import time
from datetime import date

from .ai import AIClient, AIError, extract_json

SYSTEM = (
    "You are Daybook's assistant. You run privately on the user's own computer. "
    "Work only from the material you are given. Be concise, concrete and practical. "
    "Write in Markdown. Never invent facts, names, numbers or dates."
)

MAX_SINGLE_PASS = 18_000  # characters (~4.5k tokens)


def _today_line(today: str) -> str:
    d = date.fromisoformat(today)
    return f"Today is {d.strftime('%A')}, {d.isoformat()}."


def _note_block(title: str, body: str) -> str:
    return f'<note title="{(title or "Untitled").replace(chr(34), "")}">\n{body}\n</note>'


def _glossary_hint(text: str) -> str:
    """The user's own definitions for terms in this material, so the model spells and reads them right."""
    try:
        from . import glossary

        h = glossary.hint(text)
    except Exception:  # noqa: BLE001 - the actions work without it
        return ""
    return f"\n\n{h}" if h else ""


def _msgs(user: str, system: str = SYSTEM) -> list[dict]:
    return [{"role": "system", "content": system + _glossary_hint(user)}, {"role": "user", "content": user}]


class Timer:
    def __init__(self, model: str) -> None:
        self.model = model
        self.t0 = time.perf_counter()

    def stats(self) -> dict:
        return {"model": self.model, "ms": int((time.perf_counter() - self.t0) * 1000)}


async def condense(client: AIClient, model: str, text: str, purpose: str) -> str:
    """Map-reduce long documents so they fit the model's context window."""
    if len(text) <= MAX_SINGLE_PASS:
        return text
    parts, size = [], 12_000
    paras = text.split("\n\n")
    buf = ""
    for p in paras:
        if len(buf) + len(p) > size and buf:
            parts.append(buf)
            buf = ""
        buf += p + "\n\n"
    if buf.strip():
        parts.append(buf)
    notes = []
    for i, part in enumerate(parts[:24], 1):
        out = await client.chat(_msgs(
            f"This is part {i} of {len(parts)} of a long document. Extract everything relevant for {purpose}: "
            f"key facts, figures, decisions, action items with owners and dates, and named dates. "
            f"Use terse bullet points.\n\n<part>\n{part}\n</part>"), model=model, temperature=0.1)
        notes.append(f"### Part {i}\n{out}")
    return "\n\n".join(notes)


# ---------------------------------------------------------------------------
# Note actions
# ---------------------------------------------------------------------------
async def summarize(client, models, title, body) -> dict:
    t = Timer(models["smart"])
    text = await condense(client, models["smart"], body, "a summary")
    out = await client.chat(_msgs(
        "Summarize this note. Return Markdown in this shape:\n"
        "**In short:** one or two sentences.\n\n**Key points**\n- three to seven bullets\n\n"
        "Add a **Decisions** or **Open questions** list only if the note has them.\n\n" + _note_block(title, text)),
        model=models["smart"])
    return {"markdown": out, **t.stats()}


ACTION_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"},
        "due": {"type": ["string", "null"]},
        "priority": {"type": "string", "enum": ["none", "low", "medium", "high"]},
        "owner": {"type": ["string", "null"]},
    }, "required": ["title"]}}},
    "required": ["items"],
}


async def action_items(client, models, title, body, today) -> dict:
    t = Timer(models["smart"])
    text = await condense(client, models["smart"], body, "action items")
    raw = await client.chat(_msgs(
        f"{_today_line(today)} List the concrete action items in this note: things someone needs to do. "
        "Use short imperative titles (12 words max). Give a due date as YYYY-MM-DD only if the note states or clearly "
        "implies one, otherwise null. Include the owner if named. Skip items already checked off (- [x]). "
        "Return JSON only.\n\n" + _note_block(title, text)),
        model=models["smart"], temperature=0.1, json_schema=ACTION_SCHEMA)
    data = extract_json(raw)
    items = []
    for it in (data.get("items") or [])[:30]:
        title_ = str(it.get("title") or "").strip()
        if not title_:
            continue
        due = it.get("due")
        try:
            due = date.fromisoformat(due).isoformat() if due else None
        except (ValueError, TypeError):
            due = None
        items.append({"title": title_[:200], "due": due, "priority": it.get("priority") or "none",
                      "owner": it.get("owner") or None})
    return {"items": items, **t.stats()}


META_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "tags"],
}


async def suggest_meta(client, models, title, body, existing_tags: list[str]) -> dict:
    t = Timer(models["fast"])
    raw = await client.chat(_msgs(
        "Suggest a short, specific title (8 words max) and 2 to 5 lowercase tags for this note. "
        f"Reuse these existing tags when they fit: {', '.join(existing_tags[:60]) or 'none yet'}. "
        "Return JSON only.\n\n" + _note_block(title, body[:MAX_SINGLE_PASS])),
        model=models["fast"], temperature=0.2, json_schema=META_SCHEMA)
    data = extract_json(raw)
    tags = [str(x).strip().lstrip("#").lower() for x in (data.get("tags") or []) if str(x).strip()][:6]
    return {"title": str(data.get("title") or "").strip()[:120], "tags": tags, **t.stats()}


EVENTS_SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array", "items": {"type": "object", "properties": {
        "title": {"type": "string"}, "date": {"type": "string"},
        "start_time": {"type": ["string", "null"]}, "end_time": {"type": ["string", "null"]},
        "all_day": {"type": "boolean"}, "location": {"type": ["string", "null"]},
    }, "required": ["title", "date"]}}},
    "required": ["events"],
}


def _clean_events(raw_events) -> list[dict]:
    out = []
    for ev in (raw_events or [])[:20]:
        try:
            d = date.fromisoformat(str(ev.get("date"))[:10]).isoformat()
        except (ValueError, TypeError):
            continue
        st = ev.get("start_time") or None
        en = ev.get("end_time") or None
        valid = lambda s: isinstance(s, str) and len(s) == 5 and s[2] == ":" and s.replace(":", "").isdigit()  # noqa: E731
        st = st if valid(st) else None
        en = en if valid(en) else None
        out.append({"title": str(ev.get("title") or "Event").strip()[:200], "date": d, "start_time": st,
                    "end_time": en, "all_day": bool(ev.get("all_day")) or st is None,
                    "location": (ev.get("location") or "")[:200]})
    return out


async def find_events(client, models, title, body, today) -> dict:
    t = Timer(models["smart"])
    text = await condense(client, models["smart"], body, "dates, meetings and deadlines")
    raw = await client.chat(_msgs(
        f"{_today_line(today)} Find meetings, appointments, deadlines and other dated items in this note. "
        "Only include items with a specific date; resolve relative dates against today. Times as 24-hour HH:MM, "
        "or null when no time is given. Return JSON only.\n\n" + _note_block(title, text)),
        model=models["smart"], temperature=0.1, json_schema=EVENTS_SCHEMA)
    return {"events": _clean_events(extract_json(raw).get("events")), **t.stats()}


CLEANUP_STYLES = {
    "structure": "Reorganize this note into clean Markdown with clear headings, short paragraphs and lists. "
                 "Keep every fact, number, name and link. Don't add new information.",
    "concise": "Rewrite this note to about half its length. Keep all key facts, decisions and action items.",
    "fix": "Fix spelling, grammar and punctuation only. Keep the wording, structure and Markdown otherwise unchanged.",
    "meeting": "Format this as meeting notes with sections: Summary, Attendees (if mentioned), Discussion, Decisions, "
               "and Action items as a '- [ ] ' checklist with owners and dates where stated. Keep every fact.",
    "transcript": "This is a raw transcript or voice memo. Turn it into readable notes: a two-sentence summary, key "
                  "points grouped under headings, then action items as a '- [ ] ' checklist. Remove filler words. "
                  "Keep every fact.",
}


async def cleanup(client, models, title, body, style) -> dict:
    instruction = CLEANUP_STYLES.get(style)
    if not instruction:
        raise AIError("Unknown clean-up style.")
    t = Timer(models["smart"])
    if len(body) > MAX_SINGLE_PASS * 2:
        raise AIError("This note is too long to rewrite in one go. Split it into smaller notes first.")
    out = await client.chat(_msgs(
        instruction + " Return only the rewritten note, without a title line and without commentary.\n\n" +
        _note_block(title, body)), model=models["smart"])
    return {"markdown": out, **t.stats()}


TRANSFORMS = {
    "improve": "Improve the clarity and flow of this text. Keep its meaning and Markdown formatting.",
    "shorten": "Make this text shorter and tighter. Keep the key information.",
    "expand": "Expand this into a fuller explanation. Don't invent specific facts, names or figures.",
    "bullets": "Turn this into a concise bulleted list.",
    "simplify": "Rewrite this in plain, simple language.",
    "professional": "Rewrite this in a clear, professional tone suitable for a work email.",
    "explain": "Explain what this means in plain language, briefly.",
    "checklist": "Turn this into a '- [ ] ' checklist of concrete actions.",
}


async def transform(client, models, text, action, custom: str | None = None) -> dict:
    instruction = TRANSFORMS.get(action) or (custom or "").strip()
    if not instruction:
        raise AIError("Tell the assistant what to do with the selection.")
    model = models["fast"] if action in ("bullets", "checklist", "shorten") else models["smart"]
    t = Timer(model)
    out = await client.chat(_msgs(
        f"{instruction}\nReturn only the replacement text.\n\n<text>\n{text[:MAX_SINGLE_PASS]}\n</text>"), model=model)
    return {"markdown": out, **t.stats()}


async def draft(client, models, prompt, context: str | None = None) -> dict:
    t = Timer(models["smart"])
    ctx = f"\n\nUse this material from the user's notes where relevant:\n<material>\n{context[:MAX_SINGLE_PASS]}\n</material>" if context else ""
    out = await client.chat(_msgs(
        "Write a note for the request below. Start with a level-1 heading as the title. Use Markdown structure "
        "(headings, lists, '- [ ] ' checklists where useful). Keep it practical. Where specifics are unknown, leave "
        f"clearly marked placeholders like [owner] instead of inventing them.{ctx}\n\nRequest: {prompt}"),
        model=models["smart"], temperature=0.5)
    title = ""
    lines = out.strip().splitlines()
    if lines and lines[0].startswith("# "):
        title = lines[0][2:].strip()
        out = "\n".join(lines[1:]).lstrip()
    return {"title": title, "markdown": out, **t.stats()}


async def continue_writing(client, models, title, body) -> dict:
    t = Timer(models["smart"])
    out = await client.chat(_msgs(
        "Continue this note from where it ends, in the same voice and format, with one or two short paragraphs or "
        "list items. Return only the new text.\n\n" + _note_block(title, body[-MAX_SINGLE_PASS:])),
        model=models["smart"], temperature=0.6)
    return {"markdown": out, **t.stats()}


def ask_note_messages(title, body, question) -> list[dict]:
    return _msgs("Answer the question using only this note. If the note doesn't contain the answer, say so plainly.\n\n"
                 + _note_block(title, body[:MAX_SINGLE_PASS * 2]) + f"\n\nQuestion: {question}")


def ask_notes_messages(question: str, sources: list[dict], history: list[dict] | None = None) -> list[dict]:
    excerpts = "\n\n".join(f"[{s['n']}] {s['title']}\n{s['text']}" for s in sources)
    system = SYSTEM + (
        " You answer questions about the user's own notes. Cite the numbered excerpts inline like [1] or [2][3] right "
        "after the facts they support. If the excerpts don't contain the answer, say you couldn't find it in the notes."
    )
    msgs = [{"role": "system", "content": system + _glossary_hint(f"{question}\n{excerpts}")}]
    for h in (history or [])[-6:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:4000]})
    msgs.append({"role": "user", "content": f"<excerpts>\n{excerpts or '(no matching notes)'}\n</excerpts>\n\nQuestion: {question}"})
    return msgs


async def briefing(client, models, context: str) -> dict:
    t = Timer(models["fast"])
    out = await client.chat(_msgs(
        "Write a short, plain briefing (120 words max) for the user's day from this schedule, task list and recent "
        "notes. Point out tight gaps or overlaps between events, overdue tasks, and suggest one focus. "
        "Don't invent anything.\n\n" + context), model=models["fast"], temperature=0.4)
    return {"markdown": out, **t.stats()}


IMPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "action_items": ACTION_SCHEMA["properties"]["items"],
        "events": EVENTS_SCHEMA["properties"]["events"],
    },
    "required": ["title", "summary", "tags"],
}


async def structure_import(client, models, filename, body, today, existing_tags) -> dict:
    t = Timer(models["smart"])
    text = await condense(client, models["smart"], body, "filing this document")
    raw = await client.chat(_msgs(
        f"{_today_line(today)} The user just imported the file '{filename}'. Help them file it:\n"
        "- title: a clear, specific title (10 words max)\n"
        "- summary: Markdown — one sentence on what this is, then 3 to 6 key-point bullets\n"
        f"- tags: 2 to 5 lowercase tags; reuse these when they fit: {', '.join(existing_tags[:60]) or 'none yet'}\n"
        "- action_items: concrete to-dos with due dates (YYYY-MM-DD) only when stated\n"
        "- events: dated meetings or deadlines (24-hour HH:MM times or null)\n"
        "Return JSON only.\n\n" + _note_block(filename, text)),
        model=models["smart"], temperature=0.1, json_schema=IMPORT_SCHEMA)
    data = extract_json(raw)
    items = []
    for it in (data.get("action_items") or [])[:20]:
        if str(it.get("title") or "").strip():
            due = it.get("due")
            try:
                due = date.fromisoformat(due).isoformat() if due else None
            except (ValueError, TypeError):
                due = None
            items.append({"title": str(it["title"]).strip()[:200], "due": due, "priority": it.get("priority") or "none"})
    return {
        "title": str(data.get("title") or "").strip()[:150],
        "summary": str(data.get("summary") or "").strip(),
        "tags": [str(x).strip().lstrip("#").lower() for x in (data.get("tags") or []) if str(x).strip()][:6],
        "action_items": items,
        "events": _clean_events(data.get("events")),
        **t.stats(),
    }


QUICK_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": ["event", "task", "note"]},
        "title": {"type": "string"},
        "date": {"type": ["string", "null"]},
        "start_time": {"type": ["string", "null"]},
        "end_time": {"type": ["string", "null"]},
        "all_day": {"type": "boolean"},
        "location": {"type": ["string", "null"]},
        "priority": {"type": "string", "enum": ["none", "low", "medium", "high"]},
    },
    "required": ["type", "title"],
}


async def quick_parse(client, models, text, now_iso) -> dict:
    t = Timer(models["fast"])
    raw = await client.chat(_msgs(
        f"It is now {now_iso}. Interpret this quick-capture text as a calendar event (has a time or is an "
        "appointment), a task (something to do, maybe with a due date) or a note. Resolve relative dates. Use "
        "YYYY-MM-DD dates and 24-hour HH:MM times, or null. Return JSON only.\n\n"
        f"Text: {text[:1000]}"), model=models["fast"], temperature=0.0, json_schema=QUICK_SCHEMA)
    return {**extract_json(raw), **t.stats()}


# ---------------------------------------------------------------------------
# Library: dating documents, transcripts → meeting notes, weekly review
# ---------------------------------------------------------------------------
DATE_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {"type": ["string", "null"]},
        "end": {"type": ["string", "null"]},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["date", "reason"],
}


async def pick_date(client, models, *, path: str, title: str, head: str, clues: list[dict], mentions: list[dict],
                    today: str) -> dict:
    """Ask the local model which day a document belongs to. The caller verifies the answer."""
    model = models.get("fast") or models["smart"]
    t = Timer(model)
    clue_lines = "\n".join(f"- {c['date']}{' to ' + c['end'] if c.get('end') else ''}: {c.get('evidence') or c['source']} "
                           f"(from {c['source']}, score {c['score']:.2f})" for c in clues[:8]) or "- none"
    mention_lines = "\n".join(f"- {m['date']}: {m['snippet'][:140]}" for m in mentions[:10]) or "- none"
    raw = await client.chat(_msgs(
        f"{_today_line(today)} Decide which calendar day this document belongs to: the meeting, call or event it records "
        "or prepares for. If it isn't about a dated event, use the day it was written or shared.\n"
        f"File: {path}\nTitle: {title}\n\nClues found by rules (may be wrong):\n{clue_lines}\n\n"
        f"Other dates the document mentions:\n{mention_lines}\n\n"
        f"The start of the document:\n<doc>\n{head[:3500]}\n</doc>\n\n"
        "Reply with JSON: date (YYYY-MM-DD or null), end (YYYY-MM-DD for a multi-day event, else null), "
        "confidence (0 to 1) and reason (one short sentence). Only give a date that the clues or the text support; "
        "use null if nothing does."), model=model, temperature=0.0, json_schema=DATE_SCHEMA)
    data = extract_json(raw)

    def iso(v):
        try:
            return date.fromisoformat(str(v)[:10]).isoformat() if v else None
        except ValueError:
            return None
    conf = data.get("confidence")
    try:
        conf = max(0.0, min(1.0, float(conf)))
    except (TypeError, ValueError):
        conf = 0.5
    return {"date": iso(data.get("date")), "end": iso(data.get("end")), "confidence": conf,
            "reason": str(data.get("reason") or "").strip()[:240], **t.stats()}


async def transcript_to_notes(client, models, *, title: str, transcript: str, day: str | None, time_: str | None,
                              folder: str, today: str) -> dict:
    """Raw recording transcript -> meeting notes in the house format (details table, decisions, actions)."""
    t = Timer(models["smart"])
    text = await condense(client, models["smart"], transcript, "meeting notes")
    when = f"{day}{' (recording started ' + time_ + ')' if time_ else ''}" if day else "unknown"
    out = await client.chat(_msgs(
        f"{_today_line(today)} Turn this raw meeting transcript into clear meeting notes. The recording is from {when}; "
        f"it was filed under '{folder or 'Notes'}'. Speakers aren't labelled, so attribute only when context makes it clear.\n\n"
        "Use exactly this Markdown structure:\n"
        "# <a specific meeting title>\n\n"
        "| Item | Detail |\n| --- | --- |\n| Meeting date | <weekday, month day, year (recording started about h:mm AM/PM)> |\n"
        "| Format | <what kind of meeting> |\n| Attendees | <names, as far as the transcript shows> |\n| Purpose | <one line> |\n\n"
        "## 1. Summary\n<3 to 5 sentences>\n\n## 2. Discussion\n<short subsections with bullets>\n\n"
        "## 3. Decisions\n| # | Decision |\n| --- | --- |\n\n## 4. Action items\n| Action | Owner | Notes |\n| --- | --- | --- |\n\n"
        "## 5. Open questions\n- …\n\n"
        "Keep names, numbers, systems and dates exactly as said. Leave a section's table empty rather than inventing rows.\n\n"
        f"<transcript title=\"{title.replace(chr(34), '')}\">\n{text}\n</transcript>"), model=models["smart"], temperature=0.2)
    return {"markdown": out.strip() + "\n", **t.stats()}


async def week_review(client, models, *, week_label: str, context: str) -> dict:
    t = Timer(models["smart"])
    out = await client.chat(_msgs(
        f"Write a short weekly review for {week_label} from the user's own notes below (meeting notes, decks, decisions "
        "and action items). Markdown, in this shape:\n**The week in one line:** …\n\n**What happened**\n- 4 to 8 bullets, "
        "each naming the meeting or document\n\n**Decisions**\n- …\n\n**Open threads and follow-ups**\n- owner: item\n\n"
        "**Coming up** (dates mentioned for after this week)\n- date: item\n\nOnly use what's in the notes.\n\n" + context),
        model=models["smart"], temperature=0.2)
    return {"markdown": out, **t.stats()}


async def diff_summary(client, models, *, old_title: str, new_title: str, diff_text: str) -> dict:
    model = models.get("fast") or models["smart"]
    t = Timer(model)
    out = await client.chat(_msgs(
        f"Two versions of a document: '{old_title}' (older) and '{new_title}' (newer). Below is a line diff "
        "(+ added, - removed). Summarize what changed in 3 to 8 bullets: content added, removed or reworded, "
        "and any structural changes. Be specific.\n\n<diff>\n" + diff_text[:14000] + "\n</diff>"),
        model=model, temperature=0.1)
    return {"markdown": out, **t.stats()}
