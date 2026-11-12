"""Reader endpoints: open a document in its original form, page-level search inside
it, and highlights/comments (plus gathering them into a note)."""
from __future__ import annotations

import gzip
import json
from urllib.parse import quote

from anyio import to_thread
from fastapi import APIRouter, Body, Request
from fastapi.responses import FileResponse, Response

from ..services import reader

router = APIRouter(prefix="/api")

RAW_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp"}
SANDBOX = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"


@router.get("/reader/{note_id}")
def reader_manifest(note_id: str):
    return reader.manifest(note_id)


@router.get("/reader/{note_id}/slides")
async def reader_slides(note_id: str, request: Request, limit: int | None = None):
    # Drawing a large deck the first time takes a moment; keep the server responsive.
    def work() -> tuple[bytes, bool]:
        body = json.dumps(reader.deck(note_id, limit), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        # A big deck is over a megabyte of markup; squeezed it's about a tenth, which matters on a phone.
        if len(body) > 4096 and "gzip" in request.headers.get("accept-encoding", "").lower():
            return gzip.compress(body, 6), True
        return body, False

    body, packed = await to_thread.run_sync(work)
    headers = {"Vary": "Accept-Encoding"}
    if packed:
        headers["Content-Encoding"] = "gzip"
    return Response(body, media_type="application/json", headers=headers)


@router.get("/reader/{note_id}/media/{name}")
def reader_media(note_id: str, name: str):
    path = reader.media(note_id, name)
    ext = path.suffix.lower()
    return FileResponse(path, media_type=RAW_TYPES.get(ext, "application/octet-stream"),
                        headers={"Content-Security-Policy": SANDBOX})


@router.get("/reader/{note_id}/raw")
def reader_raw(note_id: str, download: bool = False):
    src = reader.raw(note_id)
    ext = src.path.suffix.lower()
    mime = RAW_TYPES.get(ext)
    disp = "attachment" if download or not mime else "inline"
    headers = {"Content-Security-Policy": SANDBOX,
               "Content-Disposition": f"{disp}; filename*=UTF-8''{quote(src.name)}"}
    return FileResponse(src.path, media_type=mime or "application/octet-stream", headers=headers)


@router.get("/reader/{note_id}/sheet")
async def reader_sheet(note_id: str, name: str | None = None, offset: int = 0, limit: int = 500):
    return await to_thread.run_sync(lambda: reader.sheet(note_id, name, offset, limit))


@router.get("/reader/{note_id}/find")
async def reader_find(note_id: str, q: str = ""):
    return {"hits": await to_thread.run_sync(reader.find, note_id, q)}


@router.get("/reader/{note_id}/annotations")
def reader_annotations(note_id: str):
    return {"items": reader.list_annotations(note_id)}


@router.post("/reader/{note_id}/annotations")
def reader_add_annotation(note_id: str, data: dict = Body(...)):
    return reader.add_annotation(note_id, data)


@router.post("/reader/{note_id}/highlights-note")
def reader_highlights_note(note_id: str):
    return reader.save_highlights_note(note_id)


@router.get("/annotations")
def all_annotations(q: str | None = None, limit: int = 200):
    return {"items": reader.all_annotations(q, limit)}


@router.put("/annotations/{aid}")
def update_annotation(aid: str, data: dict = Body(...)):
    return reader.update_annotation(aid, data)


@router.delete("/annotations/{aid}")
def delete_annotation(aid: str):
    reader.delete_annotation(aid)
    return {"ok": True}
