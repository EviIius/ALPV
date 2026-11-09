"""Daybook test suite.

    .venv/bin/pip install pytest
    .venv/bin/python -m pytest -q

Uses a temporary data folder and a mock Ollama server; nothing touches your
real notes, and no AI model is needed.
"""
from __future__ import annotations

import json
import socket
import threading
import time
import zipfile
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from app import db, security
from app.main import create_app
from app.services import ai as ai_svc, backup, converters, events, nlparse, scheduler

FIX = Path(__file__).parent / "fixtures"
H = {"X-Daybook": "1"}
PASS = "correct horse battery"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def mock_ai():
    from tests.mock_ollama import app as mock_app

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(mock_app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.1).close()
            break
        except OSError:
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True


@pytest.fixture()
def client(tmp_path, mock_ai):
    app = create_app(tmp_path / "data", background=True)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        r = c.post("/api/auth/setup", json={"passphrase": PASS, "code": security.setup_code()}, headers=H)
        assert r.status_code == 200, r.text
        c.put("/api/settings", json={"ai": {"enabled": True, "base_url": mock_ai}, "import": {"inbox_enabled": False}}, headers=H)
        yield c


def wait_jobs(c, n=1, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = c.get("/api/import/jobs").json()
        if len(jobs) >= n and all(j["status"] in ("done", "error") for j in jobs):
            return jobs
        time.sleep(0.1)
    raise AssertionError(f"imports didn't finish: {c.get('/api/import/jobs').json()}")


# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------
def test_requires_session_and_csrf_header(tmp_path):
    app = create_app(tmp_path / "d", background=False)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        assert c.get("/api/notes").status_code == 401
        # Setup from a non-local client needs the console code.
        assert c.post("/api/auth/setup", json={"passphrase": PASS}, headers=H).status_code == 403
        assert c.post("/api/auth/setup", json={"passphrase": PASS, "code": security.setup_code()}, headers=H).status_code == 200
        assert c.post("/api/auth/setup", json={"passphrase": PASS, "code": security.setup_code()}, headers=H).status_code == 409
        assert c.post("/api/notes", json={"title": "x"}).status_code == 403  # no X-Daybook header
        assert c.post("/api/notes", json={"title": "x"}, headers=H).status_code == 200
        c.post("/api/auth/logout", headers=H)
        assert c.get("/api/notes").status_code == 401
        assert c.post("/api/auth/login", json={"passphrase": "wrong passphrase"}, headers=H).status_code == 401
        assert c.post("/api/auth/login", json={"passphrase": PASS}, headers=H).status_code == 200


def test_security_headers(client):
    r = client.get("/")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "connect-src 'self'" in csp and "script-src 'self'" in csp
    assert r.headers["x-frame-options"] == "DENY"
    assert client.get("/api/notes").headers["cache-control"] == "no-store"


def test_no_third_party_urls_in_frontend():
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    offenders = []
    for f in list(static.rglob("*.html")) + list(static.rglob("*.css")) + list((static / "js").rglob("*.js")):
        text = f.read_text("utf-8", "ignore")
        for bad in ("cdn.", "unpkg.com", "googleapis", "gstatic", "jsdelivr", "cdnjs"):
            if bad in text:
                offenders.append((f.name, bad))
    assert not offenders


def test_login_throttle():
    t = security.Throttle()
    for _ in range(6):
        t.fail("1.2.3.4")
    assert t.wait_seconds("1.2.3.4") > 0
    t.ok("1.2.3.4")
    assert t.wait_seconds("1.2.3.4") == 0


def test_ai_endpoint_must_be_local():
    assert ai_svc.check_local_endpoint("http://127.0.0.1:11434")["ok"]
    assert ai_svc.check_local_endpoint("http://192.168.1.20:1234/v1")["ok"]
    assert ai_svc.check_local_endpoint("http://100.101.102.103:11434")["ok"]  # Tailscale
    assert not ai_svc.check_local_endpoint("http://8.8.8.8:11434")["ok"]
    assert not ai_svc.check_local_endpoint("https://1.1.1.1/v1")["ok"]
    assert not ai_svc.check_local_endpoint("ftp://127.0.0.1")["ok"]


def test_ai_client_refuses_public_endpoint():
    client = ai_svc.AIClient("openai", "https://1.1.1.1/v1")
    with pytest.raises(ai_svc.AIBlocked):
        ai_svc.run_sync(client.chat([{"role": "user", "content": "hi"}], model="x"))


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
def test_note_crud_search_conflict_and_revisions(client):
    n = client.post("/api/notes", json={"title": "Lineage", "body": "# Lineage\n\nRASP feeds **FCTDS** nightly.", "tags": ["Data", "#datahub"]}, headers=H).json()
    assert n["tags"] == ["data", "datahub"] and n["word_count"] >= 4
    hits = client.get("/api/notes", params={"q": "fctd"}).json()["items"]
    assert hits and hits[0]["id"] == n["id"] and "<mark>" in hits[0]["snippet_html"]
    r = client.put(f"/api/notes/{n['id']}", json={"body": "Totally different text " * 40, "base_rev": n["rev"]}, headers=H)
    assert r.status_code == 200
    stale = client.put(f"/api/notes/{n['id']}", json={"body": "stale edit", "base_rev": n["rev"]}, headers=H)
    assert stale.status_code == 409 and stale.json()["note"]["id"] == n["id"]
    revs = client.get(f"/api/notes/{n['id']}/revisions").json()
    assert revs and "RASP" in client.get(f"/api/notes/{n['id']}/revisions/{revs[0]['id']}").json()["body"]
    restored = client.post(f"/api/notes/{n['id']}/revisions/{revs[0]['id']}/restore", headers=H).json()
    assert "RASP" in restored["body"]


def test_backlinks_trash_and_restore(client):
    a = client.post("/api/notes", json={"title": "Target"}, headers=H).json()
    b = client.post("/api/notes", json={"title": "Source", "body": f"See [Target](#/notes/{a['id']})."}, headers=H).json()
    assert [x["id"] for x in client.get(f"/api/notes/{a['id']}").json()["backlinks"]] == [b["id"]]
    client.delete(f"/api/notes/{b['id']}", headers=H)
    assert client.get("/api/notes", params={"view": "trash"}).json()["total"] == 1
    assert client.get(f"/api/notes/{a['id']}").json()["backlinks"] == []
    client.post(f"/api/notes/{b['id']}/restore", headers=H)
    assert client.get("/api/notes", params={"view": "trash"}).json()["total"] == 0


def test_locked_notes_are_encrypted_at_rest(client):
    n = client.post("/api/notes", json={"title": "Secret", "body": "account recovery words"}, headers=H).json()
    r = client.post(f"/api/notes/{n['id']}/lock", json={"passphrase": "vault passphrase"}, headers=H)
    assert r.status_code == 200 and r.json()["locked"] and r.json()["body"] == ""
    raw = db.q1("SELECT body, cipher FROM notes WHERE id = ?", (n["id"],))
    assert raw["body"] == "" and "recovery" not in raw["cipher"]
    assert not client.get("/api/notes", params={"q": "recovery"}).json()["items"]
    assert client.post(f"/api/notes/{n['id']}/unlock", json={"passphrase": "nope nope"}, headers=H).status_code == 403
    assert client.post(f"/api/notes/{n['id']}/unlock", json={"passphrase": "vault passphrase"}, headers=H).json()["body"] == "account recovery words"
    client.post(f"/api/notes/{n['id']}/save-locked", json={"passphrase": "vault passphrase", "title": "Secret", "body": "new words"}, headers=H)
    assert client.post(f"/api/notes/{n['id']}/unlock", json={"passphrase": "vault passphrase"}, headers=H).json()["body"] == "new words"
    assert client.post(f"/api/ai/note/{n['id']}/summarize", json={}, headers=H).status_code == 423
    client.post(f"/api/notes/{n['id']}/remove-lock", json={"passphrase": "vault passphrase"}, headers=H)
    assert client.get(f"/api/notes/{n['id']}").json()["body"] == "new words"


def test_daily_note_and_templates(client):
    d = client.get(f"/api/daily/{date.today().isoformat()}").json()
    assert d["kind"] == "daily" and d["linked_date"] == date.today().isoformat()
    assert client.get(f"/api/daily/{date.today().isoformat()}").json()["id"] == d["id"]
    ids = [t["id"] for t in client.get("/api/templates").json()]
    assert "meeting" in ids and "incident" in ids
    m = client.post("/api/notes", json={"template_id": "meeting"}, headers=H).json()
    assert "## Action items" in m["body"]


# ---------------------------------------------------------------------------
# Calendar & tasks
# ---------------------------------------------------------------------------
def test_recurring_events_exceptions_and_scopes(client):
    ev = client.post("/api/events", json={"title": "Standup", "start": "2026-11-09T09:15", "end": "2026-11-09T09:30",
                                          "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"}, headers=H).json()
    occ = client.get("/api/events", params={"start": "2026-11-09", "end": "2026-11-15"}).json()
    assert [o["start"] for o in occ] == [f"2026-11-{d:02d}T09:15" for d in (9, 10, 11, 12, 13)]
    client.delete(f"/api/events/{ev['id']}", params={"scope": "this", "occurrence": "2026-11-11T09:15"}, headers=H)
    client.put(f"/api/events/{ev['id']}", params={"scope": "this", "occurrence": "2026-11-12T09:15"},
               json={"start": "2026-11-12T10:00", "end": "2026-11-12T10:30"}, headers=H)
    occ = client.get("/api/events", params={"start": "2026-11-09", "end": "2026-11-15"}).json()
    assert sorted(o["start"] for o in occ) == ["2026-11-09T09:15", "2026-11-10T09:15", "2026-11-12T10:00", "2026-11-13T09:15"]
    client.delete(f"/api/events/{ev['id']}", params={"scope": "following", "occurrence": "2026-11-13T09:15"}, headers=H)
    later = client.get("/api/events", params={"start": "2026-11-16", "end": "2026-11-30"}).json()
    assert not [o for o in later if o["title"] == "Standup"]


def test_all_day_multi_day_event_overlap(client):
    client.post("/api/events", json={"title": "Offsite", "all_day": True, "start": "2026-11-18", "end": "2026-11-20"}, headers=H)
    for day, expect in (("2026-11-17", 0), ("2026-11-19", 1), ("2026-11-20", 1), ("2026-11-21", 0)):
        assert len(client.get("/api/events", params={"start": day, "end": day}).json()) == expect, day


def test_ics_import_export_roundtrip(client):
    with open(FIX / "cal.ics", "rb") as f:
        res = client.post("/api/events/import", files={"file": ("cal.ics", f, "text/calendar")}, headers=H).json()
    assert res == {"created": 2, "updated": 0, "skipped": 0}
    occ = client.get("/api/events", params={"start": "2026-11-15", "end": "2026-12-31"}).json()
    planning = [o for o in occ if o["title"] == "Quarterly planning"]
    assert len(planning) == 3 and planning[0]["reminders"] == [15] and planning[0]["location"] == "Room 4"
    holiday = next(o for o in occ if o["title"] == "Holiday")
    assert holiday["all_day"] and holiday["end"] == "2026-11-27"
    text = client.get("/api/calendar.ics").text
    assert "Quarterly planning" in text and "RRULE:FREQ=WEEKLY;COUNT=3" in text
    again = client.post("/api/events/import", files={"file": ("cal.ics", (FIX / "cal.ics").read_bytes(), "text/calendar")}, headers=H).json()
    assert again["updated"] == 2 and again["created"] == 0


def test_tasks_views_and_quick_add(client):
    today = date.today()
    client.post("/api/tasks", json={"title": "Overdue thing", "due_date": (today - timedelta(days=2)).isoformat()}, headers=H)
    client.post("/api/tasks", json={"title": "Later thing", "due_date": (today + timedelta(days=3)).isoformat(), "priority": "high"}, headers=H)
    client.post("/api/tasks", json={"title": "Someday thing"}, headers=H)
    counts = client.get("/api/tasks", params={"view": "today"}).json()["counts"]
    assert counts["overdue"] == 1 and counts["upcoming"] == 1 and counts["someday"] == 1
    parsed = client.post("/api/quick/parse", json={"text": "Lunch with Kelly tomorrow 12:30-1:30pm @ Panera #work"}, headers=H).json()
    assert parsed["type"] == "event" and parsed["start_time"] == "12:30" and parsed["end_time"] == "13:30"
    made = client.post("/api/quick/create", json=parsed, headers=H).json()
    assert made["type"] == "event" and made["item"]["calendar_id"]  # #work matched the Work calendar


@pytest.mark.parametrize("text,kind,expect", [
    ("Call the bank friday", "task", {"title": "Call the bank"}),
    ("Standup every weekday 9:15am for 15m", "event", {"start_time": "09:15", "end_time": "09:30", "rrule": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"}),
    ("Flight to Denver 2026-12-20 6:05am", "event", {"date": "2026-12-20", "start_time": "06:05", "title": "Flight to Denver"}),
    ("task: send cost deck by 11/14 !!", "task", {"date": "2026-11-14", "priority": "medium"}),
    ("Mom birthday Dec 3", "event", {"all_day": True, "date": "2026-12-03"}),
    ("buy milk", "task", {"date": None}),
])
def test_nlparse(text, kind, expect):
    r = nlparse.parse(text, datetime(2026, 11, 8, 20, 10))
    assert r["type"] == kind
    for k, v in expect.items():
        assert r[k] == v, (k, r)


def test_reminders_fire_once(client):
    now = datetime(2026, 11, 9, 9, 5)
    client.post("/api/events", json={"title": "Standup", "start": "2026-11-09T09:15", "end": "2026-11-09T09:30", "reminders": [10]}, headers=H)
    client.post("/api/tasks", json={"title": "Pay rent", "due_date": "2026-11-09", "due_time": "10:00", "remind": 60}, headers=H)
    assert scheduler.check_reminders(now) == 2
    assert scheduler.check_reminders(now) == 0
    titles = [n["title"] for n in client.get("/api/notifications").json()]
    assert "Standup" in titles and "Pay rent" in titles


def test_external_notifications_are_redacted(client, monkeypatch):
    sent = []
    from app.services import notify

    monkeypatch.setattr(notify, "_discord", lambda cfg, t, b: sent.append((t, b)))
    db.update_settings({"notifications": {"discord": {"enabled": True, "webhook": "https://discord.com/api/webhooks/1/abc"}, "macos": False}})
    notify.send("Meet the auditors", "In 15 min (3:00 PM). Room 4", redacted_body="An event is coming up.")
    assert sent == [("Daybook reminder", "An event is coming up.")]


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------
def test_converters_produce_markdown():
    docx = converters.convert(FIX / "review.docx", "review.docx")
    assert docx.title == "Q4 SAS Storage Review"
    assert "| Library | Size (TB) | Owner |" in docx.markdown and "- Storage grew 18%" in docx.markdown and docx.assets
    pdf = converters.convert(FIX / "report.pdf", "report.pdf")
    assert pdf.title == "Data Lineage Report" and "- Missing owner for 3 tables" in pdf.markdown
    pptx = converters.convert(FIX / "plan.pptx", "plan.pptx")
    assert "## 1. Cost Reduction Plan" in pptx.markdown and "Speaker notes" in pptx.markdown
    xlsx = converters.convert(FIX / "costs.xlsx", "costs.xlsx")
    assert "## Libraries" in xlsx.markdown and "## Seats" in xlsx.markdown
    eml = converters.convert(FIX / "audit.eml", "audit.eml")
    assert eml.title == "Audit schedule" and eml.assets[0].name == "data.csv"
    html = converters.convert(FIX / "clip.html", "clip.html")
    assert "alert(1)" not in html.markdown and "**world**" in html.markdown
    md = converters.convert(FIX / "memo.md", "memo.md")
    assert md.title == "Memo from front matter"
    with pytest.raises(converters.ConversionError):
        converters.convert(FIX / "memo.md", "thing.xyz")


def test_import_pipeline_with_ai_suggestions(client):
    files = [("files", (name, (FIX / name).read_bytes())) for name in ("review.docx", "report.pdf", "plan.pptx", "costs.xlsx", "audit.eml", "people.csv", "clip.html", "blue.png")]
    assert client.post("/api/import", files=files, data={"ai_mode": "suggest"}, headers=H).status_code == 200
    jobs = wait_jobs(client, len(files))
    assert all(j["status"] == "done" for j in jobs), jobs
    docx_job = next(j for j in jobs if j["filename"] == "review.docx")
    note = client.get(f"/api/notes/{docx_job['note_id']}").json()
    assert note["kind"] == "import" and note["source_name"] == "review.docx"
    assert {a["role"] for a in note["attachments"]} == {"original", "inline"}
    assert "/api/attachments/" in note["body"] and "asset://" not in note["body"]
    sug = note["ai_suggestions"]
    assert sug["title"] == "Q4 SAS storage review" and sug["tags"] == ["sas", "cost-reduction"]
    assert sug["action_items"][0]["due"] == "2026-11-14" and sug["events"][0]["date"] == "2026-11-20"
    att = next(a for a in note["attachments"] if a["role"] == "original")
    r = client.get(f"/api/attachments/{att['id']}")
    assert r.status_code == 200 and r.headers["content-disposition"].startswith("attachment")


def test_import_auto_mode_creates_tasks(client):
    client.post("/api/import", files=[("files", ("review.docx", (FIX / "review.docx").read_bytes()))], data={"ai_mode": "auto"}, headers=H)
    job = wait_jobs(client)[0]
    note = client.get(f"/api/notes/{job['note_id']}").json()
    assert note["title"] == "Q4 SAS storage review" and "sas" in note["tags"] and note["summary"]
    assert [t["title"] for t in note["tasks"]] == ["Send the cost deck"]


def test_zip_vault_import(client):
    client.post("/api/import", files=[("files", ("vault.zip", (FIX / "vault.zip").read_bytes()))], data={"ai_mode": "off"}, headers=H)
    job = wait_jobs(client)[0]
    assert job["status"] == "done" and "Imported 2 notes" in job["message"]
    names = {b["name"] for b in client.get("/api/notebooks").json()}
    assert {"Work", "Personal"} <= names


def test_inbox_folder_watcher(client, tmp_path):
    from app.services.importer import watcher

    inbox = tmp_path / "Inbox"
    db.update_settings({"import": {"inbox_enabled": True, "inbox_dir": str(inbox)}})
    watcher.scan()
    (inbox / "memo.md").write_bytes((FIX / "memo.md").read_bytes())
    (inbox / "~$lock.docx").write_bytes(b"temp")
    import os

    past = time.time() - 10
    os.utime(inbox / "memo.md", (past, past))
    watcher.scan()
    assert watcher.scan() == ["memo.md"]
    wait_jobs(client)
    assert (inbox / "Imported" / "memo.md").exists() and (inbox / "~$lock.docx").exists()


# ---------------------------------------------------------------------------
# Local AI features (mock model)
# ---------------------------------------------------------------------------
def test_ai_status_and_auto_model_pick(client):
    st = client.get("/api/ai/status").json()
    assert st["ok"] and st["local"]["ok"]
    assert st["picked"] == {"smart": "mock-large:14b", "fast": "mock-small:4b", "embed": "nomic-embed-text:latest", "vision": ""}


def test_ai_note_actions(client):
    n = client.post("/api/notes", json={"title": "Storage", "body": "We will archive old datasets. Jake sends the deck Friday."}, headers=H).json()
    s = client.post(f"/api/ai/note/{n['id']}/summarize", json={}, headers=H).json()
    assert s["markdown"].startswith("**In short:**") and "<think>" not in s["markdown"]
    a = client.post(f"/api/ai/note/{n['id']}/actions", json={}, headers=H).json()
    assert a["items"][0]["title"] == "Send the cost deck to Kelly"
    m = client.post(f"/api/ai/note/{n['id']}/meta", json={}, headers=H).json()
    assert m["title"] == "Storage cost review" and "sas" in m["tags"]
    e = client.post(f"/api/ai/note/{n['id']}/events", json={}, headers=H).json()
    assert e["events"][0]["date"] == "2026-12-03"
    q = client.post("/api/ai/quick", json={"text": "dinner with sam thursday 7pm"}, headers=H).json()
    assert q["type"] == "event" and q["start_time"] == "19:00"


def test_ask_streams_answer_with_sources(client):
    client.post("/api/notes", json={"title": "SAS decision", "body": "We decided to archive datasets older than 24 months."}, headers=H)
    from app.services.scheduler import index_some

    assert index_some() >= 1
    r = client.post("/api/ai/ask", json={"question": "What did we decide about archiving datasets?"}, headers=H)
    lines = [json.loads(x) for x in r.text.strip().splitlines()]
    assert lines[0]["type"] == "sources" and lines[0]["sources"][0]["title"] == "SAS decision"
    answer = "".join(x["text"] for x in lines if x["type"] == "delta")
    assert "[1]" in answer and lines[-1]["type"] == "done" and lines[-1]["retrieval"] == "semantic + keyword"


def test_think_filter_streaming():
    f = ai_svc.ThinkFilter()
    out = "".join(f.feed(p) for p in ["<thi", "nk>secret plan</th", "ink>Hello", " world"]) + f.flush()
    assert out == "Hello world"


# ---------------------------------------------------------------------------
# Backups & migration
# ---------------------------------------------------------------------------
def test_export_and_restore_roundtrip(client, tmp_path):
    n = client.post("/api/notes", json={"title": "Keep me", "body": "Important **text**", "tags": ["x"]}, headers=H).json()
    client.post("/api/events", json={"title": "Review", "start": "2026-11-20T10:00", "end": "2026-11-20T11:00"}, headers=H)
    blob = client.get("/api/export/zip").content
    with zipfile.ZipFile(BytesIO(blob)) as z:
        names = z.namelist()
        assert "daybook.json" in names and "calendar.ics" in names
        md = z.read(next(x for x in names if x.endswith("Keep me.md"))).decode()
        assert md.startswith("---\ntitle: \"Keep me\"") and "Important **text**" in md
    enc = client.post("/api/export/encrypted", json={"passphrase": "backup passphrase"}, headers=H).content
    assert enc[:4] == b"DBK1"
    app2 = create_app(tmp_path / "other", background=False)
    with TestClient(app2, base_url="http://127.0.0.1") as c2:
        c2.post("/api/auth/setup", json={"passphrase": PASS, "code": security.setup_code()}, headers=H)
        bad = c2.post("/api/restore", files={"file": ("x.daybook", enc)}, data={"passphrase": "wrong one!"}, headers=H)
        assert bad.status_code == 400
        res = c2.post("/api/restore", files={"file": ("x.daybook", enc)}, data={"passphrase": "backup passphrase"}, headers=H).json()
        assert res["notes"] >= 1 and res["events"] == 1
        assert c2.get(f"/api/notes/{n['id']}").json()["body"] == "Important **text**"


def test_legacy_v02_backup_import(client):
    legacy = {
        "events": [
            {"id": "evt-1", "title": "Design Sync & Feature Planning", "date": "2026-11-08", "startTime": "09:30", "endTime": "10:30", "category": "work"},
            {"id": "evt-171", "title": "Dentist", "date": "2026-11-12", "allDay": False, "startTime": "15:00", "endTime": "16:00",
             "category": "personal", "notifyDiscord": True, "discordAlertTime": "1h"},
        ],
        "notes": [
            {"id": "note-1", "title": "Project Roadmap & Discord Integration", "htmlContent": "<p>sample</p>"},
            {"id": "note-17", "title": "Groceries", "htmlContent": '<h2>List</h2><div class="task-item"><input type="checkbox" class="task-checkbox" checked><span class="task-text">Eggs</span></div><ul><li>Milk</li></ul>',
             "tags": ["home"], "pinned": True, "linkedDate": "2026-11-09", "color": "emerald", "updatedAt": 1762300000000},
        ],
        "version": "0.2.0",
    }
    res = client.post("/api/restore/legacy", json=legacy, headers=H).json()
    assert res == {"notes": 1, "events": 1, "skipped": 2}
    note = client.get("/api/notes", params={"q": "Groceries"}).json()["items"][0]
    full = client.get(f"/api/notes/{note['id']}").json()
    assert "- [x] Eggs" in full["body"] and "## List" in full["body"] and full["pinned"] and full["color"] == "green"
    ev = client.get("/api/events", params={"start": "2026-11-12", "end": "2026-11-12"}).json()[0]
    assert ev["title"] == "Dentist" and ev["reminders"] == [60] and ev["calendar"] == "Personal"


def test_snapshot_backup(client):
    path = backup.snapshot()
    assert path.exists() and path.stat().st_size > 0
    assert client.get("/api/backup/snapshots").json()[0]["name"] == path.name


def test_privacy_checkup(client):
    checks = {c["key"]: c for c in client.get("/api/privacy").json()["checks"]}
    assert checks["ai"]["status"] == "ok" and checks["external"]["status"] == "ok" and checks["signin"]["status"] == "ok"


def test_settings_mask_secrets(client):
    client.put("/api/settings", json={"notifications": {"discord": {"enabled": False, "webhook": "https://discord.com/api/webhooks/123/secretTOKEN"}}}, headers=H)
    masked = client.get("/api/settings").json()["notifications"]["discord"]["webhook"]
    assert "secret" not in masked and masked.endswith("OKEN")
    client.put("/api/settings", json={"notifications": {"discord": {"enabled": True, "webhook": masked}}}, headers=H)
    assert db.get_settings()["notifications"]["discord"]["webhook"].endswith("secretTOKEN")
    assert client.put("/api/settings", json={"notifications": {"discord": {"webhook": "https://evil.example/hook"}}}, headers=H).status_code == 400
