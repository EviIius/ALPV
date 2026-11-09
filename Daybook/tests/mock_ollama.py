"""A tiny stand-in for Ollama, used by the tests (no real model needed).

Implements /api/version, /api/tags, /api/chat (streaming and not) and
/api/embed with deterministic outputs.

    python tests/mock_ollama.py 11555
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI()
CALLS: list[dict] = []


def _vec(text: str, dim: int = 64) -> list[float]:
    words = re.findall(r"\w+", text.lower())
    v = [0.0] * dim
    for w in words:
        h = int(hashlib.md5(w.encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _answer(messages: list[dict], fmt) -> str:
    user = messages[-1]["content"] if messages else ""
    if fmt is not None:
        if "Decide which calendar day" in user:
            return json.dumps({"date": "2026-10-19", "end": None, "confidence": 0.82, "reason": "The file was last changed that day and nothing says otherwise."})
        if '"items"' in json.dumps(fmt) and "action items" in user.lower() and "imported" not in user.lower():
            return json.dumps({"items": [{"title": "Send the cost deck to Kelly", "due": "2026-11-14", "priority": "high", "owner": "Jake"},
                                         {"title": "Review entitlements with procurement", "due": None, "priority": "medium"}]})
        if "imported the file" in user:
            return "<think>planning</think>" + json.dumps({
                "title": "Q4 SAS storage review", "summary": "Meeting notes on cutting SAS storage.\n\n- Storage grew 18%\n- 42 unused seats",
                "tags": ["sas", "cost-reduction"],
                "action_items": [{"title": "Send the cost deck", "due": "2026-11-14", "priority": "high"}],
                "events": [{"title": "Entitlement review", "date": "2026-11-20", "start_time": "10:00", "end_time": None, "all_day": False, "location": None}],
            })
        if "quick-capture" in user:
            return json.dumps({"type": "event", "title": "Dinner with Sam", "date": "2026-11-12", "start_time": "19:00", "end_time": "21:00", "all_day": False, "location": "Luigi's", "priority": "none"})
        if '"events"' in json.dumps(fmt):
            return json.dumps({"events": [{"title": "Audit kickoff", "date": "2026-12-03", "start_time": "10:00", "end_time": "11:00", "all_day": False, "location": "Room 4"}]})
        if '"tags"' in json.dumps(fmt):
            return "```json\n" + json.dumps({"title": "Storage cost review", "tags": ["sas", "costs", "planning"]}) + "\n```"
        return "{}"
    if "raw meeting transcript" in user:
        return ("# SAS Workspace Sync\n\n| Item | Detail |\n| --- | --- |\n| Meeting date | Friday, October 23, 2026 (recording started about 2:30 PM) |\n"
                "| Attendees | Kelly, Jake, AJ |\n\n## 1. Summary\nThey agreed on next steps.\n\n## 3. Decisions\n| # | Decision |\n| --- | --- |\n"
                "| 1 | AJ owns the cleanup |\n\n## 4. Action items\n| Action | Owner | Notes |\n| --- | --- | --- |\n| Send the workspace list | Kelly | by 10/30 |\n")
    if "weekly review" in user:
        return "**The week in one line:** SAS cleanup moved forward.\n\n**What happened**\n- Kickoff with Kelly"
    if "Two versions of a document" in user:
        return "- Added a storage slide\n- Removed the old timeline"
    if "Summarize this note" in user:
        return "<think>Let me read.</think>**In short:** The team plans to cut SAS storage costs.\n\n**Key points**\n- Storage grew 18%\n- 42 unused seats"
    if "excerpts" in user:
        return "You decided to archive datasets older than 24 months [1]. Storage grew 18% [1]."
    if "briefing" in user.lower():
        return "You have a light day. Focus on the cost deck."
    if "Rewrite" in user or "Improve" in user or "Make this" in user or "Reorganize" in user:
        return "## Rewritten\n\nA cleaner version of the text."
    return "Here is a helpful local answer."


@app.get("/api/version")
def version():
    return {"version": "0.0.0-mock"}


@app.get("/api/tags")
def tags():
    return {"models": [
        {"name": "mock-small:4b", "size": 2_500_000_000, "details": {"family": "mock", "parameter_size": "4B", "quantization_level": "Q4_K_M"}},
        {"name": "mock-large:14b", "size": 9_000_000_000, "details": {"family": "mock", "parameter_size": "14B", "quantization_level": "Q4_K_M"}},
        {"name": "nomic-embed-text:latest", "size": 270_000_000, "details": {"family": "nomic-bert", "parameter_size": "137M"}},
    ]}


@app.post("/api/chat")
async def chat(request: Request):
    body = await request.json()
    CALLS.append(body)
    text = _answer(body.get("messages", []), body.get("format"))
    if not body.get("stream"):
        return {"model": body["model"], "message": {"role": "assistant", "content": text}, "done": True}

    def gen():
        for i in range(0, len(text), 7):
            yield json.dumps({"message": {"role": "assistant", "content": text[i:i + 7]}, "done": False}) + "\n"
        yield json.dumps({"message": {"role": "assistant", "content": ""}, "done": True}) + "\n"
    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/api/embed")
async def embed(request: Request):
    body = await request.json()
    inputs = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {"embeddings": [_vec(t) for t in inputs]}


@app.get("/_calls")
def calls():
    return JSONResponse(CALLS[-20:])


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 11555, log_level="warning")
