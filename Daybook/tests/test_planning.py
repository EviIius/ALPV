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
