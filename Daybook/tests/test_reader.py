"""The Reader: slides drawn from .pptx, PDF pages, spreadsheets, page-level search
and highlights. Decks are built with python-pptx so every feature is exercised:
placeholders inheriting from the layout, bullets, a table, a chart, a picture,
connectors and speaker notes."""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

import pytest

from app.services import reader, slides
from tests.test_daybook import FIX, H, client, mock_ai  # noqa: F401  (fixtures)


def make_deck(path: Path) -> Path:
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.dml.color import RGBColor
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
    from pptx.util import Inches, Pt

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    s1 = prs.slides.add_slide(prs.slide_layouts[0])
    s1.shapes.title.text = "SAS Storage Reduction"
    s1.placeholders[1].text = "Storage first, then entitlements"
    s1.notes_slide.notes_text_frame.text = "Walk Jacqueline through the three pieces."

    s2 = prs.slides.add_slide(prs.slide_layouts[1])
    s2.shapes.title.text = "Why storage first"
    body = s2.placeholders[1].text_frame
    body.text = "Allocated storage drives the bill"
    p = body.add_paragraph()
    p.text = "Each active GB carries a 4x footprint"
    p.level = 1
    box = s2.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(8), Inches(5), Inches(3), Inches(1))
    box.fill.solid()
    box.fill.fore_color.rgb = RGBColor(0x1F, 0x6F, 0x5B)
    box.text_frame.text = "~1.3 PB allocated"
    line = s2.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, Inches(1), Inches(6.5), Inches(6), Inches(6.5))
    line.line.color.rgb = RGBColor(0x33, 0x33, 0x33)

    s3 = prs.slides.add_slide(prs.slide_layouts[5])
    s3.shapes.title.text = "Next steps"
    rows, cols = 3, 3
    tbl = s3.shapes.add_table(rows, cols, Inches(0.5), Inches(1.6), Inches(12), Inches(2)).table
    for c, h in enumerate(("Action", "Owner", "When")):
        tbl.cell(0, c).text = h
    tbl.cell(1, 0).text = "Present the approach to Jacqueline"
    tbl.cell(1, 1).text = "Kelly"
    tbl.cell(1, 2).text = "Tue 10/20"
    tbl.cell(2, 0).text = "Pivot workspaces by owner"
    tbl.cell(2, 1).text = "Jake"
    tbl.cell(2, 2).text = "In progress"

    s4 = prs.slides.add_slide(prs.slide_layouts[5])
    s4.shapes.title.text = "Savings by piece"
    data = CategoryChartData()
    data.categories = ["Base rate", "With 4x impact"]
    data.add_series("Savings ($M)", (0.40, 1.58))
    gf = s4.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(1), Inches(1.5), Inches(6), Inches(4.5), data)
    plot = gf.chart.plots[0]
    plot.has_data_labels = True
    plot.data_labels.number_format = '"$"0.00"M"'
    plot.data_labels.number_format_is_linked = False
    s4.shapes.add_picture(str(FIX / "blue.png"), Inches(8), Inches(2), Inches(2), Inches(2))
    prs.save(path)
    return path


@pytest.fixture()
def deck_file(tmp_path):
    return make_deck(tmp_path / "Cost_Plan_Deck_v3.pptx")


# ---------------------------------------------------------------------------
# The slide renderer on its own
# ---------------------------------------------------------------------------
def test_render_deck_features(deck_file):
    r = slides.render(deck_file, lambda name: f"/m/{name}")
    assert (r.width, r.height) == (1280, 720)
    assert [s["title"] for s in r.slides] == ["SAS Storage Reduction", "Why storage first", "Next steps", "Savings by piece"]
    s1, s2, s3, s4 = r.slides
    assert s1["notes"] == "Walk Jacqueline through the three pieces."
    assert "Storage first, then entitlements" in s1["text"]
    # Body placeholder: bullets come from the master's body style; level 2 is indented further.
    assert 'class="ps-bu"' in s2["html"] and "Each active GB carries a 4x footprint" in s2["text"]
    assert "#1F6F5B" in s2["html"]  # the shape's own fill
    assert "<path" in s2["html"]    # shapes and lines are SVG
    # Table cells, chart bars with formatted labels, and the picture.
    assert "<table" in s3["html"] and "Pivot workspaces by owner" in s3["html"]
    assert "ps-chart-svg" in s4["html"] and "$1.58M" in s4["html"] and "$0.40M" in s4["html"]
    assert 'src="/m/m1.png"' in s4["html"] and r.media["m1.png"][:4] == b"\x89PNG"


def test_render_escapes_text_and_refuses_bad_files(tmp_path, deck_file):
    from pptx import Presentation

    prs = Presentation(str(deck_file))
    prs.slides[0].shapes.title.text = '<script>alert("x")</script> & "quotes"'
    evil = tmp_path / "evil.pptx"
    prs.save(evil)
    r = slides.render(evil, lambda name: name)
    html = r.slides[0]["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html and "&amp;" in html
    bad = tmp_path / "broken.pptx"
    bad.write_bytes(b"not a zip at all")
    with pytest.raises(slides.DeckError):
        slides.render(bad, lambda name: name)
    # A zip without a presentation part.
    odd = tmp_path / "odd.pptx"
    with zipfile.ZipFile(odd, "w") as z:
        z.writestr("hello.txt", "hi")
    with pytest.raises(slides.DeckError):
        slides.render(odd, lambda name: name)


def test_number_formats_and_numbering():
    f = slides.format_number
    assert f(1.58, '"$"0.00"M"') == "$1.58M"
    assert f(1234567, "#,##0") == "1,234,567"
    assert f(0.256, "0%") == "26%"
    assert f(0.256, "0.0%") == "25.6%"
    assert f(-5, "0;(0)") == "(5)"
    assert f(12, "General") == "12"
    assert f(1500000, '#,##0,,"M"') == "2M"
    assert slides.autonum("arabicPeriod", 3) == "3."
    assert slides.autonum("alphaLcParenR", 2) == "b)"
    assert slides.autonum("romanUcPeriod", 4) == "IV."
    assert slides.preset_path("chevron", 100, 50, {}).startswith("M0,0")


# ---------------------------------------------------------------------------
# Through the API: library documents
# ---------------------------------------------------------------------------
@pytest.fixture()
def lib(tmp_path, deck_file):
    root = tmp_path / "ALPV"
    for d in ("Kelly", "PPTX", "Data"):
        (root / d).mkdir(parents=True)
    shutil.copy(deck_file, root / "Kelly" / "SAS_Reduction_Plan_DRAFT_v3.pptx")
    shutil.copy(FIX / "report.pdf", root / "Data" / "Quarterly_Report.pdf")
    shutil.copy(FIX / "costs.xlsx", root / "Data" / "costs.xlsx")
    shutil.copy(FIX / "people.csv", root / "Data" / "people.csv")
    return root


def connect(c, root):
    r = c.put("/api/settings", json={"library": {"path": str(root)}}, headers=H)
    assert r.status_code == 200, r.text
    r = c.post("/api/library/rescan", json={"wait": True}, headers=H)
    assert r.status_code == 200, r.text
    items = c.get("/api/notes?view=library&limit=200").json()["items"]
    return {n["lib_path"]: n for n in items}


def test_reader_slides_pdf_and_sheets(client, lib):
    by = connect(client, lib)
    deck = by["Kelly/SAS_Reduction_Plan_DRAFT_v3.pptx"]
    m = client.get(f"/api/reader/{deck['id']}").json()
    assert m["mode"] == "slides" and m["filename"] == "SAS_Reduction_Plan_DRAFT_v3.pptx"
    d = client.get(f"/api/reader/{deck['id']}/slides").json()
    assert len(d["slides"]) == 4 and d["width"] == 1280
    pic = f"/api/reader/{deck['id']}/media/m1.png"
    assert pic in d["slides"][3]["html"]
    r = client.get(pic)
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert "sandbox" in r.headers["content-security-policy"]
    assert client.get(f"/api/reader/{deck['id']}/media/..%2F..%2Fdaybook.db").status_code in (404, 422)
    assert client.get(f"/api/reader/{deck['id']}/media/evil.svg").status_code == 422

    pdf = by["Data/Quarterly_Report.pdf"]
    assert client.get(f"/api/reader/{pdf['id']}").json()["mode"] == "pdf"
    raw = client.get(f"/api/reader/{pdf['id']}/raw")
    assert raw.status_code == 200 and raw.headers["content-type"] == "application/pdf" and raw.content[:4] == b"%PDF"

    xl = by["Data/costs.xlsx"]
    sh = client.get(f"/api/reader/{xl['id']}/sheet").json()
    assert sh["sheets"] and sh["rows"] and len(sh["rows"][0]) >= 2
    csvn = by["Data/people.csv"]
    sc = client.get(f"/api/reader/{csvn['id']}/sheet").json()
    assert sc["name"] == "people.csv" and sc["total"] >= 2

    # Wrong mode for a note gives a clear message, not a crash.
    r = client.get(f"/api/reader/{pdf['id']}/slides")
    assert r.status_code == 422 and "slide deck" in r.json()["detail"]


def test_page_search_find_and_highlights(client, lib):
    by = connect(client, lib)
    deck = by["Kelly/SAS_Reduction_Plan_DRAFT_v3.pptx"]
    assert reader.index_pending(limit=20) >= 2
    hits = client.get(f"/api/reader/{deck['id']}/find?q=jacqueline").json()["hits"]
    assert [h["page"] for h in hits] == [1, 3]   # speaker notes on slide 1, the table on slide 3
    res = client.get("/api/search?q=pivot workspaces").json()
    assert any(p["note_id"] == deck["id"] and p["page"] == 3 for p in res["pages"])

    a = client.post(f"/api/reader/{deck['id']}/annotations",
                    json={"page": 3, "quote": "Pivot workspaces by owner", "prefix": "", "suffix": "Jake",
                          "comment": "Do this before the review", "color": "green"}, headers=H).json()
    assert a["page"] == 3 and a["color"] == "green"
    b = client.post(f"/api/reader/{deck['id']}/annotations", json={"page": 1, "quote": "SAS Storage Reduction",
                                                                   "color": "nope"}, headers=H).json()
    assert b["color"] == "yellow"
    assert client.post(f"/api/reader/{deck['id']}/annotations", json={"page": 2}, headers=H).status_code == 400
    items = client.get(f"/api/reader/{deck['id']}/annotations").json()["items"]
    assert [i["page"] for i in items] == [1, 3]
    u = client.put(f"/api/annotations/{b['id']}", json={"comment": "Title slide", "quote": "ignored"}, headers=H).json()
    assert u["comment"] == "Title slide" and u["quote"] == "SAS Storage Reduction"
    assert any(h["id"] == a["id"] for h in client.get("/api/search?q=review").json()["highlights"])

    # Gather the highlights into a note in the same library folder; saving again updates it.
    n1 = client.post(f"/api/reader/{deck['id']}/highlights-note", headers=H).json()
    assert n1["title"].startswith("Highlights") and "> Pivot workspaces by owner" in n1["body"]
    assert f"#/read/{deck['id']}?p=3" in n1["body"]
    assert (lib / "Kelly" / f"{n1['title']}.md").exists() or n1.get("lib_path", "").startswith("Kelly/")
    client.delete(f"/api/annotations/{b['id']}", headers=H)
    n2 = client.post(f"/api/reader/{deck['id']}/highlights-note", headers=H).json()
    assert n2["id"] == n1["id"] and "SAS Storage Reduction\n" not in n2["body"].split("Highlights and comments")[1]
    assert client.get(f"/api/reader/{deck['id']}").json()["highlights_note"] == n1["id"]


def test_private_import_lock_removes_page_text(client, deck_file):
    from tests.test_daybook import wait_jobs

    r = client.post("/api/import", files=[("files", ("Plan.pptx", deck_file.read_bytes()))], data={"ai_mode": "off"},
                    headers=H)
    assert r.status_code == 200, r.text
    wait_jobs(client)
    nid = next(n["id"] for n in client.get("/api/notes?limit=50").json()["items"] if n["title"].startswith("SAS Storage")
               or "Plan" in n["title"])
    assert client.get(f"/api/reader/{nid}").json()["mode"] == "slides"
    assert reader.index_note(nid) == 4
    client.post(f"/api/reader/{nid}/annotations", json={"page": 2, "quote": "Allocated storage"}, headers=H)
    r = client.post(f"/api/notes/{nid}/lock", json={"passphrase": "another strong passphrase"}, headers=H)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/reader/{nid}").status_code == 423
    from app import db

    assert db.q1("SELECT COUNT(*) AS n FROM doc_pages WHERE note_id = ?", (nid,))["n"] == 0
    assert db.q1("SELECT COUNT(*) AS n FROM annotations WHERE note_id = ?", (nid,))["n"] == 0
    assert not client.get("/api/search?q=allocated").json()["pages"]


# ---------------------------------------------------------------------------
# Meeting prep and the phone calendar feed
# ---------------------------------------------------------------------------
PREP_MD = """| Item | Detail |
| --- | --- |
| Meeting date | Tuesday, October 20, 2026 |
| BDL | Kelly (led), Jake |

# Decisions

- Storage first, then entitlements

# Action items

| Action | Owner | Notes |
| --- | --- | --- |
| Pivot workspaces by owner | Jake | |
| Refresh the entitlement data | Kelly | |

# Open questions

- How does billing apply the 4x?
"""


def test_meeting_prep(client, tmp_path):
    root = tmp_path / "ALPV"
    (root / "Kelly").mkdir(parents=True)
    (root / "Kelly" / "SAS_Storage_Reduction_Notes_2026-10-20.md").write_text(PREP_MD)
    make_deck(root / "Kelly" / "SAS_Storage_Reduction_Plan.pptx")
    by = connect(client, root)
    ev = client.post("/api/events", json={"title": "SAS storage reduction with Kelly", "start": "2026-10-27T14:00",
                                           "end": "2026-10-27T15:00"}, headers=H).json()
    p = client.get(f"/api/events/{ev['id']}/prep").json()
    assert "Kelly" in p["people"]
    assert p["last"] and p["last"]["title"].startswith("SAS Storage Reduction")
    assert [a["text"] for a in p["actions"]][:1] == ["Refresh the entitlement data"]  # the attendee's own item comes first
    assert any(d["text"] == "Storage first, then entitlements" for d in p["decisions"])
    assert any("billing" in q["text"] for q in p["questions"])
    assert any(d["kind"] == "deck" for d in p["documents"])
    # An event nothing relates to gets an empty, friendly answer.
    ev2 = client.post("/api/events", json={"title": "Dentist", "start": "2026-10-28T09:00", "end": "2026-10-28T10:00"}, headers=H).json()
    p2 = client.get(f"/api/events/{ev2['id']}/prep").json()
    assert not p2["actions"] and not p2["last"]


def test_phone_calendar_feed(client):
    from fastapi.testclient import TestClient

    phone = TestClient(client.app, base_url="http://127.0.0.1", client=("100.101.102.103", 5555))  # a tailnet address
    assert client.get("/api/feed").json()["enabled"] is False
    client.post("/api/events", json={"title": "Budget review", "start": "2026-11-12T10:00", "end": "2026-11-12T11:00",
                                     "location": "Room 4"}, headers=H)
    client.post("/api/tasks", json={"title": "Send the survey", "due_date": "2026-11-13"}, headers=H)
    f = client.post("/api/feed", json={"enabled": True}, headers=H).json()
    path = f["path"]
    assert path.startswith("/feed/") and path.endswith(".ics") and len(path) > 40
    r = phone.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert "SUMMARY:Budget review" in body and "LOCATION:Room 4" in body and "Send the survey" in body
    # Busy-only hides titles and tasks.
    client.post("/api/feed", json={"detail": "busy"}, headers=H)
    body = phone.get(path).text
    assert "Budget review" not in body and "SUMMARY:Busy" in body and "Send the survey" not in body
    # Wrong token, reset link and turning it off.
    assert phone.get("/feed/not-the-token.ics").status_code == 404
    new = client.post("/api/feed/reset", headers=H).json()["path"]
    assert new != path and phone.get(path).status_code == 404 and phone.get(new).status_code == 200
    client.post("/api/feed", json={"enabled": False}, headers=H)
    assert phone.get(new).status_code == 404
    # Never answered for addresses on the public internet.
    client.post("/api/feed", json={"enabled": True}, headers=H)
    p3 = client.get("/api/feed").json()["path"]
    public = TestClient(client.app, base_url="http://127.0.0.1", client=("8.8.8.8", 5555))
    assert public.get(p3).status_code == 403


# ---------------------------------------------------------------------------
# Search operators and saved searches
# ---------------------------------------------------------------------------
def test_search_operators(client, lib):
    by = connect(client, lib)
    root = lib
    (root / "Kelly" / "Kelly 1on1 2026-10-23.md").write_text(PREP_MD)
    client.post("/api/library/rescan", json={"wait": True}, headers=H)
    # A note outside any library notebook stays private (a note in a library notebook becomes a Markdown file there).
    client.post("/api/notes", json={"title": "Grocery list", "body": "eggs, storage boxes", "notebook_id": None}, headers=H)

    def titles(q):
        r = client.get("/api/notes", params={"q": q, "limit": 50}).json()
        return {n["title"] for n in r["items"]}, r.get("filters", [])

    got, chips = titles("type:deck")
    assert got and all("Plan" in t or "Deck" in t or "SAS" in t for t in got) and chips == [{"key": "type", "value": "deck", "negated": False}]
    got, _ = titles("in:Kelly storage")
    assert got and "Grocery list" not in got
    got, _ = titles("storage -kelly")
    assert "Grocery list" in got
    got, _ = titles("person:kelly")
    assert any("1on1" in t for t in got)
    got, _ = titles("has:actions")
    assert any("1on1" in t for t in got) and "Grocery list" not in got
    got, _ = titles("after:2026-10-22 before:2026-10-24")
    assert any("1on1" in t for t in got)
    got, _ = titles('"storage boxes"')
    assert got == {"Grocery list"}
    got, _ = titles("is:private")
    assert "Grocery list" in got and not any(n.get("lib_path") for n in client.get("/api/notes", params={"q": "is:private"}).json()["items"])

    r = client.put("/api/settings", json={"search": {"saved": [{"name": "Kelly decks", "q": "in:Kelly type:deck"}, {"bad": 1}]}}, headers=H)
    assert r.status_code == 200 and r.json()["search"]["saved"] == [{"name": "Kelly decks", "q": "in:Kelly type:deck"}]
    assert "token" not in r.json().get("feed", {})
