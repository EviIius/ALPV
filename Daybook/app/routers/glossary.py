"""Glossary API: your terms and acronyms, hover definitions and "sounds like" fixes."""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ..bus import changed
from ..services import glossary, notes

router = APIRouter(prefix="/api")


@router.get("/glossary")
def list_terms(q: str = "", show: str = "all", limit: int = 500):
    if show not in ("all", "defined", "undefined", "spellings", "yours", "hidden"):
        raise HTTPException(400, "Unknown filter.")
    return glossary.list_terms(q, show, max(1, min(limit, 2000)))


@router.get("/glossary/map")
def definitions_map():
    return {"terms": glossary.definitions_map()}


@router.post("/glossary")
def add_term(payload: dict = Body(...)):
    try:
        item = glossary.save(payload.get("term", ""), payload.get("definition", ""), payload.get("aliases") or [])
    except glossary.GlossaryError as e:
        raise HTTPException(400, str(e)) from None
    changed("glossary")
    return item


@router.post("/glossary/rebuild")
def rebuild():
    res = glossary.rebuild(force=True)
    changed("glossary")
    return res


@router.put("/glossary/{term}")
def update_term(term: str, payload: dict = Body(...)):
    try:
        if "hidden" in payload and len(payload) == 1:
            item = glossary.set_hidden(term, bool(payload["hidden"]))
        else:
            item = glossary.save(term, payload.get("definition"), payload.get("aliases"), rename=payload.get("term"))
    except KeyError:
        raise HTTPException(404, "That term isn't in your glossary.") from None
    except glossary.GlossaryError as e:
        raise HTTPException(400, str(e)) from None
    changed("glossary")
    return item


@router.delete("/glossary/{term}")
def remove_term(term: str):
    try:
        res = glossary.remove(term)
    except KeyError:
        raise HTTPException(404, "That term isn't in your glossary.") from None
    changed("glossary")
    return res


@router.get("/notes/{note_id}/term-fixes")
def term_fixes(note_id: str):
    row = notes.get_row(note_id)
    if row["locked"]:
        return {"fixes": [], "editable": False}
    editable = not row.get("lib_path") or row.get("lib_kind") == "markdown"
    return {"fixes": glossary.fixes(f"{row['title'] or ''}\n{row['body'] or ''}"), "editable": editable}


@router.post("/notes/{note_id}/term-fixes")
def apply_term_fixes(note_id: str, payload: dict = Body(...)):
    row = notes.get_row(note_id)
    if row["locked"]:
        raise HTTPException(423, "Unlock this note first.")
    if row.get("lib_path") and row.get("lib_kind") != "markdown":
        raise HTTPException(409, "This file is read-only in Daybook, so its text can't be changed here.")
    body, n_body = glossary.apply_fixes(row["body"] or "", payload.get("fixes") or [])
    title, n_title = glossary.apply_fixes(row["title"] or "", payload.get("fixes") or [])
    if not (n_body or n_title):
        return {"replaced": 0, "note": notes.get(note_id)}
    patch = {}
    if n_body:
        patch["body"] = body
    if n_title:
        patch["title"] = title
    note = notes.update(note_id, patch)
    return {"replaced": n_body + n_title, "note": note}
