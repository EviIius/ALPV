"""Semantic index for "Ask your notes" and related-note suggestions.

Notes are split into overlapping chunks, embedded by a *local* embedding model
and stored in SQLite. Retrieval combines vector similarity with full-text
search (reciprocal rank fusion), so it still works — keyword-only — when no
embedding model is installed.
"""
from __future__ import annotations

import array
import math
import re
import threading

from .. import db
from ..util import md_to_text, now_ms
from .notes import content_hash, fts_query

try:  # numpy makes search fast; pure Python works too.
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

_cache_lock = threading.Lock()
_cache: dict = {"version": -1, "model": None}
_version = {"n": 0}


def _bump() -> None:
    _version["n"] += 1


def chunk_note(title: str, md: str, target: int = 1100, overlap: int = 150) -> list[str]:
    text = md or ""
    blocks: list[str] = []
    heading = ""
    for para in re.split(r"\n\s*\n", text):
        p = para.strip()
        if not p:
            continue
        if re.match(r"^#{1,6}\s", p):
            heading = re.sub(r"^#{1,6}\s+", "", p.splitlines()[0]).strip()
        blocks.append(p)
    chunks: list[str] = []
    buf = ""
    for b in blocks:
        if len(buf) + len(b) > target and buf:
            chunks.append(buf.strip())
            buf = buf[-overlap:] + "\n\n" if overlap else ""
        if len(b) > target * 1.6:
            for i in range(0, len(b), target):
                piece = b[i:i + target]
                chunks.append((buf + piece).strip())
                buf = ""
            continue
        buf += b + "\n\n"
    if buf.strip():
        chunks.append(buf.strip())
    prefix = f"{title}\n" if title else ""
    out = [prefix + md_to_text(c) for c in chunks if md_to_text(c).strip()]
    if not out and title:
        out = [title]
    return out[:200]


def _to_blob(vec) -> bytes:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return array.array("f", (v / norm for v in vec)).tobytes()


def _from_blob(blob: bytes):
    a = array.array("f")
    a.frombytes(blob)
    return a


def pending(model: str, limit: int = 8) -> list[dict]:
    rows = db.q(
        """SELECT n.id, n.title, n.body, e.content_hash AS h, e.model AS m FROM notes n
           LEFT JOIN embed_state e ON e.note_id = n.id
           WHERE n.trashed_at IS NULL AND n.locked = 0
           ORDER BY n.updated_at DESC"""
    )
    out = []
    for r in rows:
        if r["m"] != model or r["h"] != content_hash(r["title"], r["body"]):
            out.append(r)
            if len(out) >= limit:
                break
    return out


def stale_count(model: str) -> int:
    return len(pending(model, limit=100_000))


def store(note: dict, model: str, chunks: list[str], vectors: list[list[float]]) -> None:
    with db.tx() as c:
        c.execute("DELETE FROM chunks WHERE note_id = ?", (note["id"],))
        for i, (text, vec) in enumerate(zip(chunks, vectors)):
            c.execute("INSERT INTO chunks(note_id, idx, text, model, vector) VALUES (?,?,?,?,?)",
                      (note["id"], i, text, model, _to_blob(vec)))
        c.execute(
            "INSERT INTO embed_state(note_id, content_hash, model, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(note_id) DO UPDATE SET content_hash = excluded.content_hash, model = excluded.model, updated_at = excluded.updated_at",
            (note["id"], content_hash(note["title"], note["body"]), model, now_ms()),
        )
    _bump()


def clear_index() -> None:
    with db.tx() as c:
        c.execute("DELETE FROM chunks")
        c.execute("DELETE FROM embed_state")
    _bump()


def status(model: str | None) -> dict:
    total = db.scalar("SELECT count(*) FROM notes WHERE trashed_at IS NULL AND locked = 0") or 0
    if not model:
        return {"model": None, "indexed": 0, "total": total, "chunks": 0}
    stale = stale_count(model)
    return {"model": model, "indexed": total - stale, "total": total,
            "chunks": db.scalar("SELECT count(*) FROM chunks WHERE model = ?", (model,)) or 0}


def _load(model: str):
    with _cache_lock:
        if _cache["version"] == _version["n"] and _cache["model"] == model:
            return _cache
        rows = db.q(
            """SELECT c.id, c.note_id, c.text, c.vector, n.title, n.notebook_id, n.tags FROM chunks c
               JOIN notes n ON n.id = c.note_id
               WHERE c.model = ? AND n.trashed_at IS NULL AND n.locked = 0""",
            (model,),
        )
        meta = [{"id": r["id"], "note_id": r["note_id"], "text": r["text"], "title": r["title"],
                 "notebook_id": r["notebook_id"], "tags": r["tags"]} for r in rows]
        if np is not None and rows:
            mat = np.vstack([np.frombuffer(r["vector"], dtype=np.float32) for r in rows])
        else:
            mat = [_from_blob(r["vector"]) for r in rows]
        _cache.update({"version": _version["n"], "model": model, "meta": meta, "mat": mat})
        return _cache


def vector_search(query_vec: list[float], model: str, k: int = 12, scope: dict | None = None) -> list[dict]:
    cache = _load(model)
    meta = cache["meta"]
    if not meta:
        return []
    norm = math.sqrt(sum(v * v for v in query_vec)) or 1.0
    if np is not None:
        qv = np.asarray(query_vec, dtype=np.float32) / norm
        if cache["mat"].shape[1] != qv.shape[0]:
            return []
        scores = cache["mat"] @ qv
        order = np.argsort(-scores)
        pairs = ((int(i), float(scores[i])) for i in order)
    else:
        qv = [v / norm for v in query_vec]
        scored = [(i, sum(a * b for a, b in zip(row, qv))) for i, row in enumerate(cache["mat"])]
        pairs = iter(sorted(scored, key=lambda x: -x[1]))
    out = []
    for i, score in pairs:
        m = meta[i]
        if scope and not _in_scope(m, scope):
            continue
        out.append({**m, "score": score})
        if len(out) >= k:
            break
    return out


def _in_scope(m: dict, scope: dict) -> bool:
    if scope.get("notebook_id") and m.get("notebook_id") != scope["notebook_id"]:
        return False
    if scope.get("tag") and f'"{scope["tag"]}"' not in (m.get("tags") or ""):
        return False
    return True


def keyword_search(question: str, k: int = 12, scope: dict | None = None) -> list[dict]:
    words = [w for w in re.findall(r"[\w']{3,}", question.lower()) if w not in _STOP]
    if not words:
        return []
    match = " OR ".join('"' + w.replace('"', "") + '"' for w in words[:12])
    where, params = ["notes_fts MATCH ?", "n.trashed_at IS NULL", "n.locked = 0"], [match]
    if scope and scope.get("notebook_id"):
        where.append("n.notebook_id = ?")
        params.append(scope["notebook_id"])
    if scope and scope.get("tag"):
        where.append("EXISTS (SELECT 1 FROM json_each(n.tags) WHERE value = ?)")
        params.append(scope["tag"])
    rows = db.q(
        f"""SELECT n.id AS note_id, n.title, n.body FROM notes_fts JOIN notes n ON n.seq = notes_fts.rowid
            WHERE {' AND '.join(where)} ORDER BY bm25(notes_fts, 6.0, 1.0, 3.0) LIMIT ?""",
        (*params, k),
    )
    out = []
    for r in rows:
        chunks = chunk_note(r["title"], r["body"]) or [r["title"]]
        best = max(chunks, key=lambda c: sum(c.lower().count(w) for w in words))
        out.append({"note_id": r["note_id"], "title": r["title"], "text": best})
    return out


_STOP = set("""the and for are but not you all any can had her was one our out day get has him his how man new now old
see two way who boy did its let put say she too use what when where which while with this that from have they will
your about would there their them then than into could should been were what's tell show give find notes note""".split())


def retrieve(question: str, *, model: str | None, query_vec: list[float] | None, k: int = 8,
             scope: dict | None = None) -> list[dict]:
    """Hybrid retrieval → numbered sources, at most two chunks per note."""
    ranked: dict[str, dict] = {}

    def add(items, weight):
        for rank, it in enumerate(items):
            key = f"{it['note_id']}::{it['text'][:80]}"
            entry = ranked.setdefault(key, {**it, "rrf": 0.0})
            entry["rrf"] += weight / (60 + rank)

    if model and query_vec is not None:
        add(vector_search(query_vec, model, k=k * 2, scope=scope), 1.0)
    add(keyword_search(question, k=k * 2, scope=scope), 0.8)
    picked, per_note = [], {}
    for it in sorted(ranked.values(), key=lambda x: -x["rrf"]):
        if per_note.get(it["note_id"], 0) >= 2:
            continue
        per_note[it["note_id"]] = per_note.get(it["note_id"], 0) + 1
        picked.append(it)
        if len(picked) >= k:
            break
    return [{"n": i + 1, "note_id": p["note_id"], "title": p["title"] or "Untitled", "text": p["text"][:1800]}
            for i, p in enumerate(picked)]


def related(note_id: str, model: str, k: int = 5) -> list[dict]:
    cache = _load(model)
    idx = [i for i, m in enumerate(cache["meta"]) if m["note_id"] == note_id]
    if not idx:
        return []
    if np is not None:
        centroid = cache["mat"][idx].mean(axis=0)
        qv = centroid.tolist()
    else:
        dim = len(cache["mat"][idx[0]])
        qv = [sum(cache["mat"][i][d] for i in idx) / len(idx) for d in range(dim)]
    seen, out = {note_id}, []
    for hit in vector_search(qv, model, k=60):
        if hit["note_id"] in seen:
            continue
        seen.add(hit["note_id"])
        out.append({"id": hit["note_id"], "title": hit["title"], "score": round(hit["score"], 3)})
        if len(out) >= k:
            break
    return out
