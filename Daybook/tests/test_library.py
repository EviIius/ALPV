"""Library (a folder read in place) and the date scrubber.

Uses a temporary folder laid out like ALPV (Notes/, Kelly/, PPTX/, Mockups/),
a temporary data folder and the mock local AI from test_daybook.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.services import datescan, meeting
from tests.test_daybook import FIX, H, client, mock_ai  # noqa: F401  (fixtures)

ROOT = Path(__file__).resolve().parents[1]

MEETING_MD = """| Item | Detail |
| --- | --- |
| Meeting date | Friday, October 23, 2026 (recording started about 2:30 PM) |
| Format | Working call |
| BDL | Kelly (led), Jake (Vic out) |
| Workspace side | Kelsey, John Crawford, AJ |

# 1. Summary

The main blocker is the stale workspace list. The cutover is still Nov 19, 2026.

# 7. Decisions

| # | Decision |
| --- | --- |
| 1 | BDL keeps a manual owner list |
| 2 | AJ is the point person |

# 8. Action items

| Action | Owner | Notes |
| --- | --- | --- |
| Set up a session with Mary Tucker | Kelly | For this group |
| Send the survey by 10/30 | Jake | |

# 9. Open questions

- When will ownership changes be processed?
"""


# ---------------------------------------------------------------------------
# Date finding (pure functions)
# ---------------------------------------------------------------------------
REF = date(2026, 11, 8)


def found(text, **kw):
    return [(f.start.isoformat(), f.end.isoformat() if f.end else None, f.time) for f in datescan.find_dates(text, REF, **kw)]


def test_filename_patterns():
    assert found("Data_Plane_Intro_Meeting_Notes_2026-10-05", filename=True) == [("2026-10-05", None, None)]
    assert found("2026-10-23_143032_000", filename=True) == [("2026-10-23", None, "14:30")]
    assert found("Risk_Data_Strategy_Workshop_Nov17-18_DRAFT_1", filename=True) == [("2026-11-17", "2026-11-18", None)]
    assert found("notes 20261015", filename=True) == [("2026-10-15", None, None)]
    assert found("Kelly sync 10-15-2026", filename=True) == [("2026-10-15", None, None)]
    assert found("standup 10_15_26", filename=True) == [("2026-10-15", None, None)]
    # Versions, ids and acronyms are not dates.
    assert found("Risk_Data_Sourcing_Deck_v6.2_2026-10-16", filename=True) == [("2026-10-16", None, None)]
    assert found("Deck_V4_vs_V2 Model_14779 CR360 Y14 v5.1", filename=True) == []


def test_prose_patterns():
    assert found("| Meeting date | Friday, October 9, 2026 (recording started about 10:30 AM) |") == [("2026-10-09", None, "10:30")]
    assert found("Two-day working session · November 17–18, 2026") == [("2026-11-17", "2026-11-18", None)]
    assert found("Oct 30 – Nov 2, 2026") == [("2026-10-30", "2026-11-02", None)]
    assert found("15 Oct 2026 and the 3rd of December") == [("2026-10-15", None, None), ("2026-12-03", None, None)]
    assert found("Decide by 11/13 and ship on 11/20/26") == [("2026-11-13", None, None), ("2026-11-20", None, None)]
    # Not dates: a sentence break, ratios, round-the-clock, bare month-year, words containing month names.
    assert found("late November to mid-February. 4 Bring every request") == []
    assert found("a 50/50 split, 24/7 support, Q4 2026, October 2026") == []
    assert found("Mayor Novak decided in December that marching orders stand") == []
    # A wrong weekday in a document must not push the date a year away.
    assert [f.start.isoformat() for f in datescan.find_dates("Wed, Oct 15 Power BI", date(2026, 10, 15))] == ["2026-10-15"]


def test_clean_title():
    assert datescan.clean_title("Data_Plane_Intro_Meeting_Notes_2026-10-05") == "Data Plane Intro Meeting Notes"
    assert datescan.clean_title("Risk_Data_Sourcing_Deck_v6.2_2026-10-16") == "Risk Data Sourcing Deck v6.2"
    assert datescan.clean_title("2026-10-23_143032_000", kind="transcript") == "Recording 2:30 PM"
    assert datescan.clean_title("2026-10-28_113748_000 Most Important", kind="transcript") == "Most Important"
    assert datescan.clean_title("index (1)") == "index (1)"


def test_decide_combines_and_explains():
    text = MEETING_MD
    d = datescan.decide(name="SAS_Workspaces_Notes_2026-10-23.docx", text=text, tz_name="America/New_York", today=REF)
    assert (d.status, d.date, d.time, d.source) == ("set", "2026-10-23", "14:30", "filename")
    assert d.confidence > 0.99
    assert any(m["date"] == "2026-11-19" for m in d.mentions)
    assert any(m["date"] == "2026-10-30" for m in d.mentions)

    # No date anywhere: git history is a suggestion, not a certainty.
    added = datetime(2026, 10, 20, 0, 20, tzinfo=timezone.utc)   # 8:20 PM Oct 19 in New York
    d = datescan.decide(name="Corporate_Risk_SAS_Strategy.docx", text="Strategy text", git_added=added,
                        tz_name="America/New_York", today=REF)
    assert (d.status, d.candidates[0]["date"]) == ("suggested", "2026-10-19")
    assert d.date == "2026-10-19"

    # Template metadata (shared by several files) and implausibly old properties are ignored.
    meta = {"created": "2026-10-15T22:14:50Z", "modified": "2026-10-16T02:50:11Z"}
    d = datescan.decide(name="Kickoff.pptx", text="", meta=meta, templates={"2026-10-15T22:14:50Z"},
                        tz_name="America/New_York", today=REF)
    assert d.status == "none" and not d.candidates
    d = datescan.decide(name="old.docx", text="", meta={"created": "2013-12-23T23:15:00Z"}, mtime=time.time(),
                        tz_name="America/New_York")
    assert all(c["source"] != "metadata" for c in d.candidates)

    # A decision in dates.json always wins.
    d = datescan.decide(name="x_2026-10-01.docx", text="", sidecar={"date": "2026-09-30", "by": "claude", "note": "why"})
    assert (d.status, d.date, d.source, d.evidence) == ("manual", "2026-09-30", "claude", "why")
    d = datescan.decide(name="x_2026-10-01.docx", text="", sidecar={"none": True})
    assert (d.status, d.date) == ("manual", None)

    # Transcripts: conversation at the top isn't a date header.
    d = datescan.decide(name="2026-10-28_113748_000 Most Important.txt", kind="transcript",
                        text="See, week of November 23rd. Let's do it.", tz_name="America/New_York", today=REF)
    assert (d.date, d.time) == ("2026-10-28", "11:37") and all(c["source"] != "content-head" for c in d.candidates)

    # Written days after the meeting: the file's own dates don't compete with the meeting date.
    made = datetime(2026, 10, 29, 4, 7, tzinfo=timezone.utc)
    d = datescan.decide(name="AIDA_Status_Notes_2026-10-26.docx", text="| Meeting date | Monday, October 26, 2026 |",
                        meta={"created": "2026-10-29T03:00:00Z", "modified": "2026-10-29T03:05:00Z"}, git_added=made,
                        tz_name="America/New_York", today=REF)
    assert (d.status, d.date, d.conflict) == ("set", "2026-10-26", False)
    # READMEs and licences aren't tied to a day.
    d = datescan.decide(name="README.md", text="Example: Topic_Notes_2026-10-23.docx, 2026-10-23, 2026-10-23", today=REF)
    assert (d.status, d.date) == ("manual", None)

    # Strong clues that disagree are flagged for a look.
    d = datescan.decide(name="Notes_2026-10-23.docx", text="| Meeting date | Monday, October 26, 2026 |", today=REF)
    assert d.status == "review" and d.conflict


def test_cluster_and_templates():
    t = 1_793_000_000
    m = {f"f{i}": t + i for i in range(5)}
    m["lonely"] = t + 10_000
    assert datescan.clustered_mtimes(m) == {f"f{i}" for i in range(5)}
    metas = {"a": {"created": "X"}, "b": {"created": "X"}, "c": {"created": "Y"}}
    assert datescan.template_fingerprints(metas) == {"X"}


def test_meeting_facts():
    f = meeting.extract(MEETING_MD, ref=date(2026, 10, 23))
    assert ["Kelly", "Jake", "Kelsey", "John Crawford", "AJ"] == f["people"][:5]
    assert f["decisions"] == ["BDL keeps a manual owner list", "AJ is the point person"]
    assert f["actions"][0]["owner"] == "Kelly"
    assert f["actions"][1]["due"] == "2026-10-30"
    assert f["questions"] == ["When will ownership changes be processed?"]
    assert "stale workspace list" in f["summary"]
    assert meeting.extract("Just some text.") is None
    assert meeting.split_names("Laura and Carl (FCRM), Abhishek, Kevin's team, BDL, others") == ["Laura", "Carl", "Abhishek"]


# ---------------------------------------------------------------------------
# The library, end to end
# ---------------------------------------------------------------------------
@pytest.fixture()
def lib(tmp_path):
    root = tmp_path / "ALPV"
    for d in ("Notes", "Kelly", "PPTX", "Mockups"):
        (root / d).mkdir(parents=True)
    shutil.copy(FIX / "review.docx", root / "Notes" / "SAS_Storage_Review_Notes_2026-10-20.docx")
    shutil.copy(FIX / "plan.pptx", root / "PPTX" / "Cost_Plan_Deck_v1_2026-10-15.pptx")
    shutil.copy(FIX / "plan.pptx", root / "PPTX" / "Cost_Plan_Deck_v2_2026-10-16.pptx")
    (root / "Kelly" / "SAS_Workspaces_Notes_2026-10-23.md").write_text(MEETING_MD)
    (root / "Kelly" / "2026-10-23_143032_000.txt").write_text("Okay. Hello. So the list is stale and AJ will own it.\n")
    (root / "Notes" / "Kelly 1on1.md").write_text("---\ntitle: Kelly 1on1\ntags: [sas]\n---\nAgenda for the week.\n")
    (root / "Mockups" / "index.html").write_text("<html><head><title>Intake Mockup</title></head><body><h1>Intake</h1>"
                                                 "<script>document.title='x'</script></body></html>")
    undated = root / "Kelly" / "Strategy_Regenerated.md"
    undated.write_text("A strategy write-up with no date in it.\n")
    stamp = datetime(2026, 10, 19, 16, 0, tzinfo=timezone.utc).timestamp()
    os.utime(undated, (stamp, stamp))
    (root / ".git").mkdir()  # pretend repo bits must never be indexed
    (root / ".git" / "config").write_text("[core]\n")
    (root / ".DS_Store").write_bytes(b"\x00")
    return root


def connect(c, root):
    r = c.put("/api/settings", json={"library": {"path": str(root)}, "timezone": "America/New_York"}, headers=H)
    assert r.status_code == 200, r.text
    r = c.post("/api/library/rescan", json={"wait": True}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["status"]


def notes_by_path(c):
    items = c.get("/api/notes?view=library&limit=200").json()["items"]
    return {n["lib_path"]: n for n in items}


def test_library_scan_dates_and_views(client, lib):
    st = connect(client, lib)
    assert st["configured"] and st["files"] == 8
    assert (lib / ".daybook" / "library.json").exists()
    by = notes_by_path(client)
    assert ".git/config" not in by and ".DS_Store" not in by
    meet = by["Kelly/SAS_Workspaces_Notes_2026-10-23.md"]
    assert (meet["linked_date"], meet["date_time"], meet["date_source"]) == ("2026-10-23", "14:30", "filename")
    assert meet["title"] == "SAS Workspaces Notes" and meet["editable"] is True
    rec = by["Kelly/2026-10-23_143032_000.txt"]
    assert rec["lib_kind"] == "transcript" and rec["title"] == "Recording 2:30 PM" and rec["editable"] is False
    doc = by["Notes/SAS_Storage_Review_Notes_2026-10-20.docx"]
    assert doc["linked_date"] == "2026-10-20" and doc["lib_kind"] == "document"
    mock = by["Mockups/index.html"]
    assert mock["lib_kind"] == "mockup"

    # Folders became notebooks.
    books = {b["lib_dir"]: b for b in client.get("/api/notebooks").json() if b.get("lib_dir") is not None}
    assert {"Notes", "Kelly", "PPTX", "Mockups"} <= set(books)

    # The calendar shows dated files and counts the dates notes mention.
    cal = client.get("/api/library/calendar?start=2026-10-01&end=2026-11-30").json()
    assert any(i["id"] == meet["id"] for i in cal["items"])
    assert cal["mentions"]["2026-11-19"]["notes"] >= 1
    assert any(m["note_id"] == meet["id"] for m in client.get("/api/library/mentions?day=2026-11-19").json())

    # Undated files wait for a decision.
    review = {r["lib_path"]: r for r in client.get("/api/library/review").json()}
    assert "Kelly/Strategy_Regenerated.md" in review
    assert review["Kelly/Strategy_Regenerated.md"]["suggestion"]["date"] == "2026-10-19"

    # Recording <-> notes by date and time.
    full = client.get(f"/api/notes/{meet['id']}").json()
    assert [r["id"] for r in full["related"]["recordings"]] == [rec["id"]]
    assert full["facts"]["decisions"][0] == "BDL keeps a manual owner list"
    assert full["lib"]["path"] == "Kelly/SAS_Workspaces_Notes_2026-10-23.md"
    # Deck versions, and comparing them.
    v2 = by["PPTX/Cost_Plan_Deck_v2_2026-10-16.pptx"]
    rel = client.get(f"/api/notes/{v2['id']}").json()["related"]
    assert rel["family_kind"] == "versions" and rel["family"][0]["version"] == "v1"
    d = client.get(f"/api/library/diff?a={rel['family'][0]['id']}&b={v2['id']}").json()
    assert d["added"] == 0 and d["removed"] == 0

    # Meeting facts across the library.
    assert client.get("/api/library/decisions?q=owner").json()[0]["note_id"] == meet["id"]
    fu = client.get("/api/library/followups?owner=jake").json()
    assert fu and fu[0]["text"].startswith("Send the survey")
    t = client.post("/api/library/followups/track", json={"note_id": meet["id"], "index": fu[0]["index"]}, headers=H).json()
    assert t["due_date"] == "2026-10-30" and t["note_id"] == meet["id"]
    assert client.get("/api/library/followups?owner=jake").json()[0]["tracked"] is True
    people = {p["name"]: p for p in client.get("/api/library/people").json()}
    assert people["Kelly"]["meetings"] == 1
    tl = client.get("/api/library/timeline?start=2026-10-01&end=2026-11-30&person=Kelsey").json()
    assert [i["id"] for i in tl["items"]] == [meet["id"]]
    assert client.get("/api/library/mockups").json()[0]["id"] == mock["id"]
    assert client.get("/api/today?day=2026-10-23").json()["linked_notes"]


def test_library_files_are_served_safely(client, lib):
    connect(client, lib)
    r = client.get("/api/library/file?path=Mockups/index.html")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "sandbox" in csp and "connect-src 'none'" in csp and r.headers["x-frame-options"] == "SAMEORIGIN"
    for bad in ("../ALPV/Notes/x", ".git/config", "/etc/passwd", ".daybook/library.json", "Notes/../../outside"):
        assert client.get("/api/library/file", params={"path": bad}).status_code in (403, 404), bad
    r = client.get("/api/library/file?path=Notes/SAS_Storage_Review_Notes_2026-10-20.docx")
    assert r.headers["content-disposition"].startswith("attachment")


def test_markdown_two_way_sync(client, lib):
    connect(client, lib)
    by = notes_by_path(client)
    n = by["Notes/Kelly 1on1.md"]
    assert n["title"] == "Kelly 1on1"
    # Daybook -> file (front matter preserved)
    r = client.put(f"/api/notes/{n['id']}", json={"body": "Agenda for the week.\n\n- [ ] Ask about storage\n"}, headers=H)
    assert r.status_code == 200
    text = (lib / "Notes" / "Kelly 1on1.md").read_text()
    assert text.startswith("---\ntitle: Kelly 1on1\ntags: [sas]\n---\n") and "Ask about storage" in text
    # file -> Daybook (and the old text is kept in history)
    time.sleep(0.01)
    (lib / "Notes" / "Kelly 1on1.md").write_text("---\ntitle: Kelly 1on1\n---\nEdited in VS Code.\n")
    client.post("/api/library/rescan", json={"wait": True}, headers=H)
    fresh = client.get(f"/api/notes/{n['id']}").json()
    assert fresh["body"] == "Edited in VS Code.\n"
    assert client.get(f"/api/notes/{n['id']}/revisions").json()
    # Both sides changed: nothing is lost, the Daybook version becomes a copy.
    (lib / "Notes" / "Kelly 1on1.md").write_text("---\ntitle: Kelly 1on1\n---\nChanged on disk again.\n")
    r = client.put(f"/api/notes/{n['id']}", json={"body": "Typed in Daybook.\n"}, headers=H).json()
    assert r.get("lib_conflict")
    assert "Changed on disk again." in (lib / "Notes" / "Kelly 1on1.md").read_text()
    assert "Typed in Daybook." in (lib / r["lib_conflict"]).read_text()
    # Renaming the note renames the file.
    r = client.put(f"/api/notes/{n['id']}", json={"title": "Kelly weekly"}, headers=H).json()
    assert r["lib_path"] == "Notes/Kelly weekly.md" and (lib / "Notes" / "Kelly weekly.md").exists()
    assert not (lib / "Notes" / "Kelly 1on1.md").exists()


def test_converted_files_are_read_only_and_trash_restores(client, lib):
    connect(client, lib)
    by = notes_by_path(client)
    doc = by["Notes/SAS_Storage_Review_Notes_2026-10-20.docx"]
    r = client.put(f"/api/notes/{doc['id']}", json={"body": "nope"}, headers=H)
    assert r.status_code == 400
    assert client.post(f"/api/notes/{doc['id']}/lock", json={"passphrase": "vault pass phrase"}, headers=H).status_code == 400
    # Trash moves the file aside; restore brings it back.
    assert client.delete(f"/api/notes/{doc['id']}", headers=H).status_code == 200
    assert not (lib / "Notes" / "SAS_Storage_Review_Notes_2026-10-20.docx").exists()
    assert list((lib / ".daybook" / "trash").rglob("SAS_Storage_Review_Notes_2026-10-20.docx"))
    client.post("/api/library/rescan", json={"wait": True}, headers=H)  # the trashed file must not look "missing"
    assert client.post(f"/api/notes/{doc['id']}/restore", headers=H).status_code == 200
    assert (lib / "Notes" / "SAS_Storage_Review_Notes_2026-10-20.docx").exists()
    # Deleted outside Daybook -> the note goes to the trash; back on disk -> restored.
    (lib / "PPTX" / "Cost_Plan_Deck_v1_2026-10-15.pptx").rename(lib / "deck.bak")
    client.post("/api/library/rescan", json={"wait": True}, headers=H)
    v1 = by["PPTX/Cost_Plan_Deck_v1_2026-10-15.pptx"]
    assert client.get(f"/api/notes/{v1['id']}").json()["trashed_at"]
    (lib / "deck.bak").rename(lib / "PPTX" / "Cost_Plan_Deck_v1_2026-10-15.pptx")
    client.post("/api/library/rescan", json={"wait": True}, headers=H)
    assert client.get(f"/api/notes/{v1['id']}").json()["trashed_at"] is None
    # Renamed/moved outside Daybook -> the same note follows the file.
    (lib / "Kelly" / "SAS_Workspaces_Notes_2026-10-23.md").rename(lib / "Notes" / "SAS_Workspaces_Notes_2026-10-23.md")
    meet = by["Kelly/SAS_Workspaces_Notes_2026-10-23.md"]
    client.post("/api/library/rescan", json={"wait": True}, headers=H)
    moved = client.get(f"/api/notes/{meet['id']}").json()
    assert moved["lib_path"] == "Notes/SAS_Workspaces_Notes_2026-10-23.md" and moved["trashed_at"] is None


def test_dates_are_saved_next_to_the_files(client, lib):
    connect(client, lib)
    by = notes_by_path(client)
    n = by["Kelly/Strategy_Regenerated.md"]
    r = client.post(f"/api/notes/{n['id']}/date", json={"date": "2026-10-19", "time": "20:20"}, headers=H).json()
    assert (r["linked_date"], r["date_status"], r["date_source"]) == ("2026-10-19", "manual", "you")
    side = json.loads((lib / ".daybook" / "dates.json").read_text())
    assert side["dates"]["Kelly/Strategy_Regenerated.md"]["date"] == "2026-10-19"
    # Edited by hand (or by Claude) -> picked up on the next scan.
    side["dates"]["Mockups/index.html"] = {"date": "2026-11-08", "by": "claude", "note": "Added with the README"}
    (lib / ".daybook" / "dates.json").write_text(json.dumps(side))
    client.post("/api/library/rescan", json={"wait": True}, headers=H)
    m = client.get(f"/api/notes/{by['Mockups/index.html']['id']}").json()
    assert (m["linked_date"], m["date_source"]) == ("2026-11-08", "claude")
    # Back to automatic.
    client.post(f"/api/notes/{n['id']}/date", json={"clear": True}, headers=H)
    assert "Kelly/Strategy_Regenerated.md" not in json.loads((lib / ".daybook" / "dates.json").read_text())["dates"]
    # Accept the rules' suggestion from the review queue.
    assert client.post(f"/api/library/dates/{n['id']}", json={"accept": True}, headers=H).status_code == 200
    assert client.get(f"/api/notes/{n['id']}").json()["date_status"] == "manual"


def test_new_notes_land_in_the_library(client, lib):
    connect(client, lib)
    n = client.post("/api/notes", json={}, headers=H).json()
    assert n.get("lib_pending") is True and not n["lib_path"]          # no empty file is written
    assert not list((lib / "Notes").glob("Untitled*"))
    r = client.put(f"/api/notes/{n['id']}", json={"title": "Prep for Nov 17 workshop", "body": "Bring the deck."}, headers=H).json()
    assert r["lib_path"] == "Notes/Prep for Nov 17 workshop.md"
    assert (lib / "Notes" / "Prep for Nov 17 workshop.md").read_text() == "Bring the deck."
    assert r["linked_date"] == "2026-11-17" and r["date_source"] in ("title", "filename")
    # Moving it to another folder moves the file.
    kelly = next(b for b in client.get("/api/notebooks").json() if b.get("lib_dir") == "Kelly")
    r = client.put(f"/api/notes/{n['id']}", json={"notebook_id": kelly["id"]}, headers=H).json()
    assert r["lib_path"] == "Kelly/Prep for Nov 17 workshop.md" and (lib / "Kelly" / "Prep for Nov 17 workshop.md").exists()
    # Private notes stay private when that's the setting.
    client.put("/api/settings", json={"library": {"new_notes": "private"}}, headers=H)
    p = client.post("/api/notes", json={"title": "Private thought", "body": "x"}, headers=H).json()
    assert not p["lib_path"] and not p.get("lib_pending")
    # Upload straight into a library folder.
    with open(FIX / "report.pdf", "rb") as fh:
        jobs = client.post("/api/import", files={"files": ("Lineage_Report_2026-11-02.pdf", fh, "application/pdf")},
                           data={"library_dir": "Notes"}, headers=H).json()
    assert jobs[0]["status"] == "done" and (lib / "Notes" / "Lineage_Report_2026-11-02.pdf").exists()
    rep = client.get(f"/api/notes/{jobs[0]['note_id']}").json()
    assert rep["linked_date"] == "2026-11-02" and rep["lib_kind"] == "pdf"


def test_local_ai_dates_and_drafts(client, lib):
    connect(client, lib)
    by = notes_by_path(client)
    n = by["Kelly/Strategy_Regenerated.md"]
    res = client.post("/api/library/dates/ai", json={"ids": [n["id"]], "apply": True}, headers=H).json()
    assert res[0]["date"] == "2026-10-19" and res[0]["verified"] is True and res[0]["applied"] is True
    note = client.get(f"/api/notes/{n['id']}").json()
    assert (note["linked_date"], note["date_source"]) == ("2026-10-19", "ai")
    assert json.loads((lib / ".daybook" / "dates.json").read_text())["dates"]["Kelly/Strategy_Regenerated.md"]["by"] == "ai"
    # Recording -> meeting notes, saved next to it and dated the same day.
    rec = by["Kelly/2026-10-23_143032_000.txt"]
    drafted = client.post(f"/api/library/transcript-notes/{rec['id']}", json={}, headers=H).json()
    assert drafted["lib_path"].startswith("Kelly/") and drafted["linked_date"] == "2026-10-23"
    assert drafted["facts"]["decisions"] == ["AJ owns the cleanup"]
    # Week in review.
    wr = client.post("/api/library/week-review", json={"start": "2026-10-19"}, headers=H).json()
    assert "week in one line" in wr["markdown"].lower()


def test_git_status_reads_without_locks(client, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "Notes").mkdir()
    (root / "Notes" / "a_2026-10-01.md").write_text("hello")
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_AUTHOR_DATE": "2026-10-01T12:00:00Z", "GIT_COMMITTER_DATE": "2026-10-01T12:00:00Z"}
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True, env=env)
    (root / "Notes" / "b.md").write_text("new")
    assert datescan.git_added_dates(root)["Notes/a_2026-10-01.md"].date() == date(2026, 10, 1)
    connect(client, root)
    g = client.get("/api/library/git").json()
    assert g["repo"] and g["branch"] == "main" and any(ch["path"].endswith("b.md") for ch in g["changes"])
    assert not list((root / ".git").glob("*.lock"))


def test_daybook_dates_command(lib):
    (lib / ".daybook").mkdir(exist_ok=True)
    (lib / ".daybook" / "library.json").write_text(json.dumps({"name": "ALPV", "timezone": "America/New_York"}))
    cli = [sys.executable, str(ROOT / "daybook-dates"), "--library", str(lib)]
    out = subprocess.run(cli + ["scan", "--json"], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    files = {f["path"]: f for f in json.loads(out.stdout)["files"]}
    assert "Kelly/Strategy_Regenerated.md" in files and "Kelly/SAS_Workspaces_Notes_2026-10-23.md" not in files
    out = subprocess.run(cli + ["set", "Kelly/Strategy_Regenerated.md", "2026-10-19", "--by", "claude", "--note", "git"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    entry = json.loads((lib / ".daybook" / "dates.json").read_text())["dates"]["Kelly/Strategy_Regenerated.md"]
    assert entry["date"] == "2026-10-19" and entry["by"] == "claude"
    out = subprocess.run(cli + ["explain", "Kelly/SAS_Workspaces_Notes_2026-10-23.md"], capture_output=True, text=True, timeout=60)
    assert "2026-10-23" in out.stdout and "filename" in out.stdout
    out = subprocess.run(cli + ["scan"], capture_output=True, text=True, timeout=60)
    assert "Strategy_Regenerated" not in out.stdout
