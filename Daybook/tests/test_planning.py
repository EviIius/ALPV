"""Making time for tasks on the calendar, and the Look back card on Today."""
from __future__ import annotations

from datetime import date, timedelta

from tests.test_daybook import H, client, mock_ai  # noqa: F401  (fixtures)


def test_time_blocking(client):
    day = (date.today() + timedelta(days=3)).isoformat()
    t = client.post("/api/tasks", json={"title": "Write the storage memo", "due_date": day}, headers=H).json()
    r = client.get(f"/api/tasks/{t['id']}/time", params={"minutes": 60}).json()
    assert r["day"] == day and r["blocks"] == []
    assert [s["start"][11:] for s in r["slots"]][:3] == ["08:00", "09:00", "10:00"]

    # A meeting 09:00-10:30 leaves 08:00 and then 10:30 onwards.
    client.post("/api/events", json={"title": "Budget review", "start": f"{day}T09:00", "end": f"{day}T10:30"}, headers=H)
    r = client.get(f"/api/tasks/{t['id']}/time", params={"day": day, "minutes": 60}).json()
    starts = [s["start"][11:] for s in r["slots"]]
    assert starts[:3] == ["08:00", "10:30", "11:30"] and "09:00" not in starts
    assert all(s["end"][11:] <= "18:00" for s in r["slots"])
    # Two hours don't fit before the meeting.
    r = client.get(f"/api/tasks/{t['id']}/time", params={"day": day, "minutes": 120}).json()
    assert r["slots"][0]["start"][11:] == "10:30"

    ev = client.post(f"/api/tasks/{t['id']}/time", json={"start": f"{day}T10:30", "minutes": 90}, headers=H).json()
    assert ev["task_id"] == t["id"] and ev["start"] == f"{day}T10:30" and ev["end"] == f"{day}T12:00"
    assert ev["title"] == "Write the storage memo"
    task = next(x for x in client.get("/api/tasks", params={"view": "open"}).json()["items"] if x["id"] == t["id"])
    assert task["blocked_at"] == f"{day}T10:30"
    r = client.get(f"/api/tasks/{t['id']}/time", params={"day": day, "minutes": 60}).json()
    assert len(r["blocks"]) == 1 and "10:30" not in [s["start"][11:] for s in r["slots"]]
    occ = client.get("/api/events", params={"start": day, "end": day}).json()
    occ = occ["items"] if isinstance(occ, dict) else occ
    assert any(o.get("task_id") == t["id"] for o in occ)
    assert client.get(f"/api/tasks/{t['id']}").json()["title"] == "Write the storage memo"
    # Deleting the task keeps the time on the calendar, unlinked.
    client.delete(f"/api/tasks/{t['id']}", headers=H)
    assert client.get(f"/api/events/{ev['id']}").json()["task_id"] is None
    assert client.post(f"/api/tasks/{t['id']}/time", json={"start": f"{day}T13:00"}, headers=H).status_code == 404


def test_look_back(client):
    client.post("/api/notes", json={"title": "Kelly 1:1", "body": "SAS storage", "linked_date": "2026-11-02", "notebook_id": None}, headers=H)
    client.post("/api/notes", json={"title": "Kickoff", "body": "Workspace owners", "linked_date": "2026-10-09", "notebook_id": None}, headers=H)
    client.post("/api/notes", json={"title": "Last year", "body": "Planning", "linked_date": "2025-11-09", "notebook_id": None}, headers=H)
    lb = client.get("/api/today", params={"day": "2026-11-09"}).json()["look_back"]
    assert [(g["label"], g["date"], [n["title"] for n in g["notes"]]) for g in lb] == [
        ("A week ago", "2026-11-02", ["Kelly 1:1"]), ("A month ago", "2026-10-09", ["Kickoff"]), ("A year ago", "2025-11-09", ["Last year"])]
    assert client.get("/api/today", params={"day": "2026-11-10"}).json()["look_back"] == []


def test_repeating_tasks(client):
    from datetime import date as D

    from app.services import tasks as T

    # The next due date: stays on the day of the month, skips ahead when long overdue, handles month ends.
    assert T.next_due("FREQ=MONTHLY", "2026-11-01", D(2026, 11, 10)) == "2026-12-01"
    assert T.next_due("FREQ=MONTHLY", "2026-01-31", D(2026, 1, 31)) == "2026-02-28"
    assert T.next_due("FREQ=WEEKLY", "2026-09-07", D(2026, 11, 10)) == "2026-11-16"      # a Monday task, months overdue
    assert T.next_due("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", "2026-11-13", D(2026, 11, 13)) == "2026-11-16"   # Friday → Monday
    assert T.next_due("FREQ=DAILY", None, D(2026, 11, 10)) == "2026-11-11"
    assert T.normalize_repeat("monthly") == "FREQ=MONTHLY" and T.normalize_repeat("") is None

    # Typed in plain words, it becomes a repeating task.
    p = client.post("/api/quick/parse", json={"text": "Pay rent on the 1st every month"}, headers=H).json()
    assert p["type"] == "task" and p["rrule"] == "FREQ=MONTHLY" and p["date"].endswith("-01")
    t = client.post("/api/quick/create", json={**p, "type": "task"}, headers=H).json()["item"]
    assert t["repeat"] == "FREQ=MONTHLY" and t["title"] == "Pay rent"

    # Ticking it off finishes this one and adds the next, with the same details.
    client.put(f"/api/tasks/{t['id']}", json={"priority": 2, "list": "Home", "remind": 60}, headers=H)
    done = client.put(f"/api/tasks/{t['id']}", json={"done": True}, headers=H).json()
    nxt = done["next"]
    assert done["done"] is True and done["repeat"] is None
    assert nxt["id"] != t["id"] and nxt["done"] is False and nxt["repeat"] == "FREQ=MONTHLY"
    assert nxt["due_date"] > t["due_date"] and nxt["due_date"].endswith("-01")
    assert (nxt["title"], nxt["priority"], nxt["list"], nxt["remind"]) == ("Pay rent", 2, "Home", 60)
    open_titles = [x["title"] for x in client.get("/api/tasks", params={"view": "open"}).json()["items"]]
    assert open_titles.count("Pay rent") == 1
    # Reopening the finished one doesn't create a second chain.
    again = client.put(f"/api/tasks/{t['id']}", json={"done": False}, headers=H).json()
    assert "next" not in client.put(f"/api/tasks/{again['id']}", json={"done": True}, headers=H).json()
    # Bad rules are refused; a repeat can be removed.
    assert client.put(f"/api/tasks/{nxt['id']}", json={"repeat": "whenever"}, headers=H).status_code == 400
    assert client.put(f"/api/tasks/{nxt['id']}", json={"repeat": ""}, headers=H).json()["repeat"] is None
