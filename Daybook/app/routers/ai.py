"""Local AI endpoints. Every call goes through AIClient's local-only guard."""
from __future__ import annotations

import asyncio
import json
import time

from anyio import to_thread
from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from .. import db
from ..services import ai_tasks, embeddings, events, notes, tasks
from ..services.ai import AIClient, AIError, check_local_endpoint, pick_models
from ..util import local_now, md_to_text, today_str

router = APIRouter(prefix="/api/ai")


async def _ready(require: str = "smart"):
    settings = await to_thread.run_sync(db.get_settings)
    if not settings["ai"].get("enabled"):
        raise HTTPException(400, "Local AI is turned off. Turn it on in Settings → Local AI.")
    client = AIClient.from_settings(settings)
    models = pick_models(settings, await client.models())
    if require and not models.get(require):
        need = {"smart": "a chat model", "fast": "a chat model", "embed": "an embedding model"}[require]
        raise HTTPException(400, f"Install {need} in your local AI app, then pick it in Settings → Local AI.")
    return settings, client, models


def _note_text(note_id: str) -> tuple[dict, str]:
    row = notes.get_row(note_id)
    if row["locked"]:
        raise HTTPException(423, "Locked notes are never sent to AI.")
    return row, row["body"]


@router.get("/status")
async def status():
    settings = await to_thread.run_sync(db.get_settings)
    client = AIClient.from_settings(settings)
    st = await client.status() if settings["ai"].get("enabled") else {"ok": False, "models": [], "error": "Local AI is off.",
                                                                   "local": check_local_endpoint(settings["ai"]["base_url"])}
    st["enabled"] = bool(settings["ai"].get("enabled"))
    st["picked"] = pick_models(settings, st.get("models") or [])
    st["index"] = await to_thread.run_sync(embeddings.status, st["picked"].get("embed"))
    return st


@router.post("/test")
async def test(payload: dict = Body(...)):
    client = AIClient(payload.get("provider", "ollama"), payload.get("base_url", ""))
    return await client.status()


@router.post("/note/{note_id}/{action}")
async def note_action(note_id: str, action: str, payload: dict = Body(default_factory=dict)):
    row, body = await to_thread.run_sync(_note_text, note_id)
    if not md_to_text(body).strip() and action != "meta":
        raise HTTPException(400, "This note is empty.")
    settings, client, models = await _ready("fast" if action == "meta" else "smart")
    title = row["title"]
    if action == "summarize":
        return await ai_tasks.summarize(client, models, title, body)
    if action == "actions":
        return await ai_tasks.action_items(client, models, title, body, today_str())
    if action == "meta":
        existing = [t["tag"] for t in await to_thread.run_sync(notes.all_tags)]
        return await ai_tasks.suggest_meta(client, models, title, body, existing)
    if action == "events":
        return await ai_tasks.find_events(client, models, title, body, today_str())
    if action == "cleanup":
        return await ai_tasks.cleanup(client, models, title, body, payload.get("style", "structure"))
    if action == "continue":
        return await ai_tasks.continue_writing(client, models, title, body)
    raise HTTPException(404, "Unknown AI action.")


@router.post("/transform")
async def transform(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Select some text first.")
    _s, client, models = await _ready("smart")
    return await ai_tasks.transform(client, models, text, payload.get("action", "improve"), payload.get("custom"))


@router.post("/draft")
async def draft(payload: dict = Body(...)):
    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(400, "Describe the note you want drafted.")
    settings, client, models = await _ready("smart")
    context = None
    if payload.get("use_notes"):
        sources = await _retrieve(client, models, prompt, k=5)
        context = "\n\n".join(f"{s['title']}:\n{s['text']}" for s in sources) or None
    return await ai_tasks.draft(client, models, prompt, context)


async def _retrieve(client, models, question, k=8, scope=None):
    vec = None
    if models.get("embed"):
        try:
            vec = (await client.embed([question], model=models["embed"]))[0]
        except AIError:
            vec = None
    return await to_thread.run_sync(lambda: embeddings.retrieve(question, model=models.get("embed"), query_vec=vec, k=k, scope=scope))


def _ndjson(obj) -> str:
    return json.dumps(obj, default=str) + "\n"


@router.post("/ask")
async def ask(payload: dict = Body(...)):
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "Ask a question.")
    settings, client, models = await _ready("smart")
    scope = payload.get("scope") or None
    sources = await _retrieve(client, models, question, k=8, scope=scope)
    messages = ai_tasks.ask_notes_messages(question, sources, payload.get("history"))

    async def gen():
        t0 = time.perf_counter()
        yield _ndjson({"type": "sources", "sources": [{k: s[k] for k in ("n", "note_id", "title")} | {"text": s["text"][:300]} for s in sources]})
        try:
            async for piece in client.stream_chat(messages, model=models["smart"]):
                yield _ndjson({"type": "delta", "text": piece})
            yield _ndjson({"type": "done", "model": models["smart"], "ms": int((time.perf_counter() - t0) * 1000),
                           "retrieval": "semantic + keyword" if models.get("embed") else "keyword"})
        except AIError as exc:
            yield _ndjson({"type": "error", "message": str(exc)})

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.post("/ask-note/{note_id}")
async def ask_note(note_id: str, payload: dict = Body(...)):
    row, body = await to_thread.run_sync(_note_text, note_id)
    question = (payload.get("question") or "").strip()
    if not question:
        raise HTTPException(400, "Ask a question.")
    _s, client, models = await _ready("smart")
    messages = ai_tasks.ask_note_messages(row["title"], body, question)

    async def gen():
        t0 = time.perf_counter()
        try:
            async for piece in client.stream_chat(messages, model=models["smart"]):
                yield _ndjson({"type": "delta", "text": piece})
            yield _ndjson({"type": "done", "model": models["smart"], "ms": int((time.perf_counter() - t0) * 1000)})
        except AIError as exc:
            yield _ndjson({"type": "error", "message": str(exc)})

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.get("/related/{note_id}")
async def related(note_id: str):
    settings = await to_thread.run_sync(db.get_settings)
    if not settings["ai"].get("enabled"):
        return []
    client = AIClient.from_settings(settings)
    try:
        models = pick_models(settings, await client.models())
    except AIError:
        return []
    if not models.get("embed"):
        return []
    return await to_thread.run_sync(embeddings.related, note_id, models["embed"])


@router.post("/briefing")
async def briefing(payload: dict = Body(default_factory=dict)):
    _s, client, models = await _ready("fast")
    from datetime import date

    d = date.fromisoformat(payload.get("date") or today_str())

    def context() -> str:
        evs = events.occurrences_between(d, d)
        open_tasks = tasks.list_tasks("open", due_to=d.isoformat())
        recent = notes.list_notes(view="all", limit=5)["items"]
        lines = [f"Date: {d.strftime('%A')}, {d.isoformat()}", "", "Schedule:"]
        lines += [f"- {'all day' if e['all_day'] else e['start'][11:16] + '-' + e['end'][11:16]} {e['title']}" + (f" @ {e['location']}" if e["location"] else "") for e in evs] or ["- nothing scheduled"]
        lines += ["", "Tasks due today or overdue:"]
        lines += [f"- {t['title']} (due {t['due_date']}{' ' + t['due_time'] if t['due_time'] else ''})" for t in open_tasks] or ["- none"]
        lines += ["", "Recently edited notes:"] + [f"- {n['title']}: {n['snippet'][:120]}" for n in recent]
        return "\n".join(lines)

    return await ai_tasks.briefing(client, models, await to_thread.run_sync(context))


@router.post("/prep-brief")
async def prep_brief(payload: dict = Body(...)):
    from ..services import prep

    _s, client, models = await _ready("fast")
    data = await to_thread.run_sync(lambda: prep.build(payload.get("event_id") or "", payload.get("start")))
    t = ai_tasks.Timer(models["fast"])
    out = await client.chat(ai_tasks._msgs(
        "Write a short prep brief (100 words max) for this meeting from the user's own notes below: what to follow up on, "
        "what was decided, what is still open, and one thing to push for. Use only these facts; don't invent names, "
        "dates or numbers.\n\n" + prep.as_context(data)), model=models["fast"], temperature=0.3)
    return {"markdown": out, **t.stats()}


@router.post("/define-term")
async def define_term(payload: dict = Body(...)):
    """What does this term mean, going only by the notes that use it? A suggestion to review, never saved by itself."""
    from ..services import glossary

    term = str(payload.get("term") or "").strip()[:60]
    if not term:
        raise HTTPException(400, "Which term?")
    _s, client, models = await _ready("fast")
    found = await to_thread.run_sync(lambda: glossary.excerpts(term))
    sources = [{"id": e["note_id"], "title": e["title"]} for e in found]
    if not found:
        return {"definition": "", "sources": [], "reason": "No unlocked note uses this term."}
    res = await ai_tasks.define_term(client, models, term, found)
    if not res["definition"]:
        res["reason"] = "Your notes use this term but never say what it means."
    return {**res, "sources": sources}


@router.post("/quick")
async def quick(payload: dict = Body(...)):
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "Type something to capture.")
    _s, client, models = await _ready("fast")
    res = await ai_tasks.quick_parse(client, models, text, local_now().strftime("%A %Y-%m-%d %H:%M"))
    res.setdefault("tags", [])
    res.setdefault("priority", "none")
    return res


@router.post("/reindex")
async def reindex():
    await to_thread.run_sync(embeddings.clear_index)
    from ..services.scheduler import index_some

    asyncio.get_running_loop().run_in_executor(None, index_some, 12)
    return {"ok": True}
