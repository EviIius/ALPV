"""SQLite storage: connections, schema migrations and settings.

One connection per thread (FastAPI runs sync endpoints in a thread pool), WAL
mode for concurrent readers, and FTS5 for full-text search.
"""
from __future__ import annotations

import copy
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import Paths, local_timezone_name, paths as default_paths

_local = threading.local()
_state: dict[str, Any] = {"paths": default_paths}
_settings_lock = threading.Lock()
_settings_cache: dict[str, Any] | None = None


def configure(p: Paths) -> None:
    """Point the app at a data directory (used by run.py and tests)."""
    global _settings_cache
    _state["paths"] = p
    _settings_cache = None
    # Force every thread to reopen against the new path.
    _state["generation"] = _state.get("generation", 0) + 1


def get_paths() -> Paths:
    return _state["paths"]


def _open(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path), timeout=10, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA synchronous = NORMAL")
    c.execute("PRAGMA busy_timeout = 10000")
    c.execute("PRAGMA temp_store = MEMORY")
    return c


def conn() -> sqlite3.Connection:
    gen = _state.get("generation", 0)
    c = getattr(_local, "conn", None)
    if c is None or getattr(_local, "gen", None) != gen:
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        c = _open(get_paths().db)
        _local.conn = c
        _local.gen = gen
    return c


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Write transaction (BEGIN IMMEDIATE so writers queue instead of failing)."""
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
        c.execute("COMMIT")
    except BaseException:
        c.execute("ROLLBACK")
        raise


def q(sql: str, params: tuple | dict = ()) -> list[dict]:
    return [dict(r) for r in conn().execute(sql, params).fetchall()]


def q1(sql: str, params: tuple | dict = ()) -> dict | None:
    r = conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def scalar(sql: str, params: tuple | dict = ()) -> Any:
    r = conn().execute(sql, params).fetchone()
    return r[0] if r else None


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
MIGRATIONS: list[str] = [
    # 1 — initial schema
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

    CREATE TABLE auth (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      pass_hash TEXT NOT NULL,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );

    CREATE TABLE sessions (
      token_hash TEXT PRIMARY KEY,
      created_at INTEGER NOT NULL,
      expires_at INTEGER NOT NULL,
      last_seen INTEGER NOT NULL,
      user_agent TEXT,
      ip TEXT
    );

    CREATE TABLE notebooks (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      color TEXT,
      sort INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );

    CREATE TABLE notes (
      seq INTEGER PRIMARY KEY AUTOINCREMENT,
      id TEXT NOT NULL UNIQUE,
      title TEXT NOT NULL DEFAULT '',
      body TEXT NOT NULL DEFAULT '',
      notebook_id TEXT REFERENCES notebooks(id) ON DELETE SET NULL,
      tags TEXT NOT NULL DEFAULT '[]',
      color TEXT,
      pinned INTEGER NOT NULL DEFAULT 0,
      archived INTEGER NOT NULL DEFAULT 0,
      trashed_at INTEGER,
      linked_date TEXT,
      kind TEXT NOT NULL DEFAULT 'note',
      source_name TEXT,
      summary TEXT,
      ai_suggestions TEXT,
      locked INTEGER NOT NULL DEFAULT 0,
      cipher TEXT,
      rev INTEGER NOT NULL DEFAULT 1,
      word_count INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE INDEX idx_notes_updated ON notes(updated_at DESC);
    CREATE INDEX idx_notes_notebook ON notes(notebook_id);
    CREATE INDEX idx_notes_linked_date ON notes(linked_date);

    CREATE VIRTUAL TABLE notes_fts USING fts5(
      title, body, tags,
      content='notes', content_rowid='seq',
      tokenize='porter unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER notes_fts_ai AFTER INSERT ON notes BEGIN
      INSERT INTO notes_fts(rowid, title, body, tags) VALUES (new.seq, new.title, new.body, new.tags);
    END;
    CREATE TRIGGER notes_fts_ad AFTER DELETE ON notes BEGIN
      INSERT INTO notes_fts(notes_fts, rowid, title, body, tags) VALUES ('delete', old.seq, old.title, old.body, old.tags);
    END;
    CREATE TRIGGER notes_fts_au AFTER UPDATE OF title, body, tags ON notes BEGIN
      INSERT INTO notes_fts(notes_fts, rowid, title, body, tags) VALUES ('delete', old.seq, old.title, old.body, old.tags);
      INSERT INTO notes_fts(rowid, title, body, tags) VALUES (new.seq, new.title, new.body, new.tags);
    END;

    CREATE TABLE note_revisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      body TEXT NOT NULL,
      created_at INTEGER NOT NULL
    );
    CREATE INDEX idx_revisions_note ON note_revisions(note_id, created_at DESC);

    CREATE TABLE note_links (
      src_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
      dst_id TEXT NOT NULL,
      PRIMARY KEY (src_id, dst_id)
    );
    CREATE INDEX idx_note_links_dst ON note_links(dst_id);

    CREATE TABLE attachments (
      id TEXT PRIMARY KEY,
      note_id TEXT REFERENCES notes(id) ON DELETE SET NULL,
      filename TEXT NOT NULL,
      mime TEXT,
      size INTEGER NOT NULL DEFAULT 0,
      sha256 TEXT,
      rel_path TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'inline',
      created_at INTEGER NOT NULL
    );
    CREATE INDEX idx_attachments_note ON attachments(note_id);

    CREATE TABLE calendars (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      color TEXT NOT NULL,
      visible INTEGER NOT NULL DEFAULT 1,
      sort INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL
    );

    CREATE TABLE events (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      location TEXT NOT NULL DEFAULT '',
      calendar_id TEXT REFERENCES calendars(id) ON DELETE SET NULL,
      all_day INTEGER NOT NULL DEFAULT 0,
      start TEXT NOT NULL,
      end TEXT NOT NULL,
      rrule TEXT,
      exdates TEXT NOT NULL DEFAULT '[]',
      reminders TEXT NOT NULL DEFAULT '[]',
      note_id TEXT REFERENCES notes(id) ON DELETE SET NULL,
      uid TEXT,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE INDEX idx_events_start ON events(start);
    CREATE INDEX idx_events_uid ON events(uid);

    CREATE TABLE tasks (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      details TEXT NOT NULL DEFAULT '',
      list TEXT,
      priority INTEGER NOT NULL DEFAULT 0,
      due_date TEXT,
      due_time TEXT,
      remind INTEGER,
      done INTEGER NOT NULL DEFAULT 0,
      done_at INTEGER,
      note_id TEXT REFERENCES notes(id) ON DELETE SET NULL,
      sort INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE INDEX idx_tasks_due ON tasks(done, due_date);

    CREATE TABLE reminder_log (key TEXT PRIMARY KEY, sent_at INTEGER NOT NULL);

    CREATE TABLE imports (
      id TEXT PRIMARY KEY,
      filename TEXT NOT NULL,
      origin TEXT NOT NULL,
      status TEXT NOT NULL,
      message TEXT,
      note_id TEXT,
      options TEXT NOT NULL DEFAULT '{}',
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );

    CREATE TABLE chunks (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
      idx INTEGER NOT NULL,
      text TEXT NOT NULL,
      model TEXT NOT NULL,
      vector BLOB NOT NULL
    );
    CREATE INDEX idx_chunks_note ON chunks(note_id);

    CREATE TABLE embed_state (
      note_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
      content_hash TEXT NOT NULL,
      model TEXT NOT NULL,
      updated_at INTEGER NOT NULL
    );

    CREATE TABLE inbox_items (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      body TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL DEFAULT 'info',
      ref TEXT,
      read INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL
    );
    """,
    # 2 — the Library (a folder of your files, read in place) and date assignment
    """
    ALTER TABLE notes ADD COLUMN date_end TEXT;
    ALTER TABLE notes ADD COLUMN date_time TEXT;
    ALTER TABLE notes ADD COLUMN date_source TEXT;
    ALTER TABLE notes ADD COLUMN date_confidence REAL;
    ALTER TABLE notes ADD COLUMN date_status TEXT;
    ALTER TABLE notes ADD COLUMN date_info TEXT;
    ALTER TABLE notes ADD COLUMN facts TEXT;
    ALTER TABLE notes ADD COLUMN lib_path TEXT;
    ALTER TABLE notes ADD COLUMN lib_kind TEXT;
    CREATE UNIQUE INDEX idx_notes_lib_path ON notes(lib_path) WHERE lib_path IS NOT NULL;
    CREATE INDEX idx_notes_date_status ON notes(date_status);
    ALTER TABLE notebooks ADD COLUMN lib_dir TEXT;

    CREATE TABLE lib_files (
      path TEXT PRIMARY KEY,
      note_id TEXT REFERENCES notes(id) ON DELETE SET NULL,
      kind TEXT NOT NULL,
      size INTEGER NOT NULL DEFAULT 0,
      mtime_ns INTEGER NOT NULL DEFAULT 0,
      sha256 TEXT NOT NULL DEFAULT '',
      synced_hash TEXT,
      status TEXT NOT NULL DEFAULT 'ok',
      error TEXT,
      meta TEXT NOT NULL DEFAULT '{}',
      first_seen INTEGER NOT NULL,
      indexed_at INTEGER NOT NULL
    );
    CREATE INDEX idx_lib_files_note ON lib_files(note_id);
    CREATE INDEX idx_lib_files_sha ON lib_files(sha256);

    CREATE TABLE date_mentions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
      date TEXT NOT NULL,
      date_end TEXT,
      kind TEXT NOT NULL DEFAULT 'mention',
      snippet TEXT NOT NULL
    );
    CREATE INDEX idx_mentions_date ON date_mentions(date);
    CREATE INDEX idx_mentions_note ON date_mentions(note_id);

    UPDATE notes SET date_source = CASE WHEN kind = 'daily' THEN 'daily' ELSE 'manual' END,
                     date_status = CASE WHEN kind = 'daily' THEN 'set' ELSE 'manual' END,
                     date_confidence = 1.0
     WHERE linked_date IS NOT NULL;
    """,
    # 3 — the Reader (highlights, page-level search), glossary, and a few links
    """
    CREATE TABLE annotations (
      id TEXT PRIMARY KEY,
      note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
      page INTEGER,
      kind TEXT NOT NULL DEFAULT 'highlight',
      quote TEXT NOT NULL DEFAULT '',
      prefix TEXT NOT NULL DEFAULT '',
      suffix TEXT NOT NULL DEFAULT '',
      comment TEXT NOT NULL DEFAULT '',
      color TEXT NOT NULL DEFAULT 'yellow',
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );
    CREATE INDEX idx_annotations_note ON annotations(note_id, page);

    CREATE TABLE doc_pages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
      page INTEGER NOT NULL,
      label TEXT NOT NULL DEFAULT '',
      text TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX idx_doc_pages_note ON doc_pages(note_id, page);
    CREATE VIRTUAL TABLE doc_pages_fts USING fts5(
      label, text,
      content='doc_pages', content_rowid='id',
      tokenize='porter unicode61 remove_diacritics 2'
    );
    CREATE TRIGGER doc_pages_fts_ai AFTER INSERT ON doc_pages BEGIN
      INSERT INTO doc_pages_fts(rowid, label, text) VALUES (new.id, new.label, new.text);
    END;
    CREATE TRIGGER doc_pages_fts_ad AFTER DELETE ON doc_pages BEGIN
      INSERT INTO doc_pages_fts(doc_pages_fts, rowid, label, text) VALUES ('delete', old.id, old.label, old.text);
    END;
    CREATE TRIGGER doc_pages_fts_au AFTER UPDATE ON doc_pages BEGIN
      INSERT INTO doc_pages_fts(doc_pages_fts, rowid, label, text) VALUES ('delete', old.id, old.label, old.text);
      INSERT INTO doc_pages_fts(rowid, label, text) VALUES (new.id, new.label, new.text);
    END;

    CREATE TABLE doc_index (
      note_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
      sha256 TEXT NOT NULL,
      kind TEXT NOT NULL,
      pages INTEGER NOT NULL DEFAULT 0,
      version INTEGER NOT NULL DEFAULT 0,
      error TEXT,
      indexed_at INTEGER NOT NULL
    );

    CREATE TABLE reading_notes (
      source_id TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
      note_id TEXT NOT NULL
    );

    CREATE TABLE glossary (
      term TEXT PRIMARY KEY COLLATE NOCASE,
      definition TEXT NOT NULL DEFAULT '',
      aliases TEXT NOT NULL DEFAULT '[]',
      source_note TEXT,
      origin TEXT NOT NULL DEFAULT 'user',
      hidden INTEGER NOT NULL DEFAULT 0,
      uses INTEGER NOT NULL DEFAULT 0,
      note_count INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL,
      updated_at INTEGER NOT NULL
    );

    ALTER TABLE events ADD COLUMN task_id TEXT;
    """,
    # 4 — repeating tasks
    """
    ALTER TABLE tasks ADD COLUMN repeat TEXT;
    """,
]


def migrate() -> int:
    c = conn()
    current = c.execute("PRAGMA user_version").fetchone()[0]
    for i, script in enumerate(MIGRATIONS, start=1):
        if i <= current:
            continue
        c.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _split_sql(script):
                c.execute(stmt)
            c.execute(f"PRAGMA user_version = {i}")
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


def _split_sql(script: str) -> list[str]:
    """Split a migration script into statements (triggers contain ';')."""
    out: list[str] = []
    buf: list[str] = []
    depth = 0
    for line in script.splitlines():
        s = line.strip()
        if not s:
            continue
        buf.append(line)
        upper = s.upper()
        if upper.startswith("CREATE TRIGGER"):
            depth = 1
        if depth and upper.startswith("END;"):
            depth = 0
            out.append("\n".join(buf))
            buf = []
            continue
        if not depth and s.endswith(";"):
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


# ---------------------------------------------------------------------------
# Settings (JSON blobs with defaults)
# ---------------------------------------------------------------------------
def default_settings() -> dict:
    return {
        "profile": {"name": ""},
        "appearance": {"theme": "system", "density": "comfortable", "week_start": 0, "time_format": "12h"},
        "timezone": local_timezone_name(),
        "calendar": {"default_duration": 60, "day_start": 7, "day_end": 22},
        "ai": {
            "enabled": True,
            "provider": "ollama",
            "base_url": "http://127.0.0.1:11434",
            "fast_model": "",
            "smart_model": "",
            "embed_model": "",
            "vision_model": "",
            "think": False,
            "temperature": 0.3,
            "num_ctx": 8192,
            "smart_import": "suggest",
            "auto_index": True,
        },
        "import": {
            "inbox_enabled": True,
            "inbox_dir": "~/Notes Inbox",
            "keep_originals": True,
            "default_notebook": "",
            "ocr": "auto",
            "transcribe": "auto",
            "whisper_model": "small",
            "max_table_rows": 300,
        },
        "notifications": {
            "default_reminder": 15,
            "inapp": True,
            "macos": True,
            "redact_external": True,
            "ntfy": {"enabled": False, "url": "", "topic": "", "token": ""},
            "discord": {"enabled": False, "webhook": ""},
        },
        "backups": {"auto": True, "keep": 14, "last": 0},
        "library": {
            "enabled": True,
            "path": "",
            "new_notes": "library",
            "new_notes_dir": "Notes",
            "daily_notes": "private",
            "date_new_notes": True,
            "ai_dates": "suggest",
            "show_mentions": True,
        },
        "vault": {"verifier": ""},
        "migration": {"legacy_imported": False},
        # Private calendar feed (an .ics address your phone's Calendar can subscribe to).
        "feed": {"enabled": False, "token": "", "detail": "full", "include_tasks": True,
                 "days_back": 30, "days_ahead": 365},
        "search": {"saved": []},
        "glossary": {"suggest_fixes": True},
    }


def _deep_merge(base: dict, patch: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (patch or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_settings() -> dict:
    global _settings_cache
    with _settings_lock:
        if _settings_cache is None:
            stored = {r["key"]: json.loads(r["value"]) for r in q("SELECT key, value FROM settings")}
            _settings_cache = _deep_merge(default_settings(), stored)
        return copy.deepcopy(_settings_cache)


def update_settings(patch: dict) -> dict:
    """Deep-merge `patch` into stored settings and persist top-level keys."""
    global _settings_cache
    current = get_settings()
    merged = _deep_merge(current, patch)
    with tx() as c:
        for key, value in merged.items():
            if key in patch:
                c.execute(
                    "INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, json.dumps(value)),
                )
    with _settings_lock:
        _settings_cache = None
    return get_settings()


def fts_available() -> bool:
    try:
        conn().execute("SELECT count(*) FROM notes_fts LIMIT 1")
        return True
    except sqlite3.Error:
        return False
