"""Glossary: definitions and transcript readings found by rule, your own terms,
"sounds like" fixes, and the hints given to local AI and Whisper."""
from __future__ import annotations

from app.services import glossary
from tests.test_daybook import H, client, mock_ai  # noqa: F401  (fixtures)

NOTE_A = """# Storage review
We moved the Statistical Analysis System (SAS) workloads. GSMOS (Global Storage Management Operating System)
costs went up. We met with Kelly (BDL) on Tuesday. The DPC team joins next week; DPC owns the cutover.

| Acronym | Meaning |
| --- | --- |
| BDL | Business data leaders: sit in each line of business |
| System of record (SOR) | The application where data is originally created |

| **Note on the transcript**  Auto-generated transcript. Readings used: FCRM ("FICRAM"), Credit 360 ("360," "Credit 362"),
Yaginesh (FCRM product; transcribed "YAG"). "GRD" is likely eGRC. |
"""

NOTE_B = """Follow-up on DPC and the FCRM rollout. FCRM owns screening.
## Where definitions come from
- Mike: creating the metadata is the most important part.
"""

TRANSCRIPT = "So FICRAM said the Credit 362 numbers are late, and Credit 360 is fine. Sure, FICRAM again."


def test_find_definitions_by_rule():
    found = glossary.find_definitions(NOTE_A + NOTE_B)
    defs = {(t, d) for t, d, _ in found if d}
    assert ("SAS", "Statistical Analysis System") in defs
    assert ("GSMOS", "Global Storage Management Operating System") in defs
    assert ("BDL", "Business data leaders: sit in each line of business") in defs
    assert any(t == "SOR" and d.startswith("System of record") for t, d in defs)
    readings = {t: v for t, d, v in found if v}
    assert readings["FCRM"] == ["FICRAM"] and readings["Credit 360"] == ["Credit 362"]
    assert readings["Yaginesh"] == ["YAG"] and readings["eGRC"] == ["GRD"]
    terms = {t for t, _, _ in found}
    # Speaker lines and people in brackets aren't definitions.
    assert "Mike" not in terms and not any(d == "BDL" for _, d, _ in found)
    assert glossary.spells("DH", ["DataHub"]) == 1 and glossary.spells("SAS", ["Sales", "team"]) == 0


def test_glossary_api(client):
    a = client.post("/api/notes", json={"title": "Storage review", "body": NOTE_A, "notebook_id": None}, headers=H).json()
    client.post("/api/notes", json={"title": "Follow-up", "body": NOTE_B, "notebook_id": None}, headers=H)
    t = client.post("/api/notes", json={"title": "Call transcript", "body": TRANSCRIPT, "notebook_id": None}, headers=H).json()
    r = client.post("/api/glossary/rebuild", headers=H).json()
    assert r["terms"] >= 6
    items = {i["term"]: i for i in client.get("/api/glossary").json()["items"]}
    assert items["SAS"]["definition"] == "Statistical Analysis System" and items["SAS"]["origin"] == "found"
    assert items["SAS"]["source_note"] == a["id"] and items["SAS"]["source_title"] == "Storage review"
    assert items["FCRM"]["aliases"] == ["FICRAM"] and items["FCRM"]["uses"] >= 3
    # DPC is used in two notes but never defined: it needs a definition.
    undefined = [i["term"] for i in client.get("/api/glossary", params={"show": "undefined"}).json()["items"]]
    assert "DPC" in undefined and "SAS" not in undefined
    assert "FCRM" in [i["term"] for i in client.get("/api/glossary", params={"show": "spellings"}).json()["items"]]

    # Your definition wins and survives a rescan; found terms you remove stay hidden.
    r = client.put("/api/glossary/DPC", json={"definition": "Data private cloud", "aliases": "future safety PC, DPCs"}, headers=H)
    assert r.status_code == 200 and r.json()["origin"] == "user" and r.json()["aliases"] == ["future safety PC", "DPCs"]
    assert client.delete("/api/glossary/GSMOS", headers=H).json() == {"hidden": True}
    client.post("/api/glossary/rebuild", headers=H)
    items = {i["term"]: i for i in client.get("/api/glossary").json()["items"]}
    assert items["DPC"]["definition"] == "Data private cloud" and "GSMOS" not in items
    assert "GSMOS" in [i["term"] for i in client.get("/api/glossary", params={"show": "hidden"}).json()["items"]]
    assert {"term": "SAS", "definition": "Statistical Analysis System"} in client.get("/api/glossary/map").json()["terms"]
    # New terms of your own; bad input is refused.
    assert client.post("/api/glossary", json={"term": "Pillar 3", "definition": "Reporting pillar"}, headers=H).status_code == 200
    assert client.post("/api/glossary", json={"term": ""}, headers=H).status_code == 400
    assert client.delete("/api/glossary/Pillar 3", headers=H).json() == {"deleted": True}

    # "Sounds like" fixes: distinctive readings only, never inside the term itself.
    fx = client.get(f"/api/notes/{t['id']}/term-fixes").json()
    pairs = {(f["variant"], f["term"]): f["count"] for f in fx["fixes"]}
    assert pairs[("FICRAM", "FCRM")] == 2 and pairs[("Credit 362", "Credit 360")] == 1 and fx["editable"]
    r = client.post(f"/api/notes/{t['id']}/term-fixes", json={"fixes": [{"variant": "FICRAM", "term": "FCRM"},
                                                                        {"variant": "Credit 362", "term": "Credit 360"}]}, headers=H).json()
    assert r["replaced"] == 3
    assert r["note"]["body"] == "So FCRM said the Credit 360 numbers are late, and Credit 360 is fine. Sure, FCRM again."
    assert client.get(f"/api/notes/{t['id']}/term-fixes").json()["fixes"] == []

    # The local AI and Whisper get the terms that matter.
    h = glossary.hint("FICRAM and SAS were discussed")
    assert "SAS = Statistical Analysis System" in h and "FCRM" in h and "FICRAM" in h
    assert glossary.hint("it was fine") == ""
    assert "FCRM" in glossary.vocabulary() and "SAS" in glossary.vocabulary()

    # Locking a note takes the definitions found only there out of the glossary.
    assert client.post(f"/api/notes/{a['id']}/lock", json={"passphrase": "correct horse battery"}, headers=H).status_code == 200
    terms = {i["term"] for i in client.get("/api/glossary").json()["items"]}
    assert "SAS" not in terms and "DPC" in terms
    client.post("/api/glossary/rebuild", headers=H)
    assert "SAS" not in {i["term"] for i in client.get("/api/glossary").json()["items"]}


def test_suggest_definition_from_notes(client):
    """The local AI may suggest a meaning, only from notes that use the term, and nothing is saved until you say so."""
    a = client.post("/api/notes", json={"title": "Cutover plan", "body": "The DPC team joins next week; DPC owns the cutover to the on-premises data platform."}, headers=H).json()
    client.post("/api/notes", json={"title": "Odd one", "body": "We keep hearing about ZZQ in standups and nobody explains ZZQ at all."}, headers=H)
    locked = client.post("/api/notes", json={"title": "Secret", "body": "DPC is the secret project budget line for next year."}, headers=H).json()
    assert client.post(f"/api/notes/{locked['id']}/lock", json={"passphrase": "vault passphrase"}, headers=H).json()["locked"]

    found = glossary.excerpts("DPC")
    assert [e["note_id"] for e in found] == [a["id"]]            # the locked note is never read
    assert "DPC owns the cutover" in found[0]["text"]

    r = client.post("/api/ai/define-term", json={"term": "DPC"}, headers=H).json()
    assert r["definition"] == "The data private cloud, the on-premises data platform."
    assert r["sources"] == [{"id": a["id"], "title": "Cutover plan"}] and r["model"]
    assert not any(t["term"] == "DPC" and t["definition"] for t in glossary.list_terms()["items"])   # a suggestion, not a save

    r = client.post("/api/ai/define-term", json={"term": "ZZQ"}, headers=H).json()
    assert r["definition"] == "" and "never say" in r["reason"] and r["sources"]
    r = client.post("/api/ai/define-term", json={"term": "NOPE9"}, headers=H).json()
    assert r["definition"] == "" and r["sources"] == []
    assert client.post("/api/ai/define-term", json={"term": " "}, headers=H).status_code == 400
