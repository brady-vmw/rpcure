"""End-to-end test with mock API payloads (no network). Run: python tests/test_app.py"""
import copy, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TMP = tempfile.mkdtemp()
os.environ["STATE_PATH"] = os.path.join(TMP, "state.json")
os.environ["SITE_URL"] = "https://example.org/rp/"
os.environ.pop("ANTHROPIC_API_KEY", None)

import fetcher, refresh, store

def study(nct, title, status, phases, sponsor, interventions, countries, cond=("Retinitis Pigmentosa",),
          has_results=False, last_update="2026-10-01", study_type="INTERVENTIONAL", summary=""):
    return {"hasResults": has_results, "protocolSection": {
        "identificationModule": {"nctId": nct, "briefTitle": title},
        "statusModule": {"overallStatus": status, "lastUpdatePostDateStruct": {"date": last_update},
                         "startDateStruct": {"date": "2025-03"}, "primaryCompletionDateStruct": {"date": "2027-06"}},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": sponsor, "class": "INDUSTRY"}},
        "conditionsModule": {"conditions": list(cond)},
        "designModule": {"studyType": study_type, "phases": phases, "enrollmentInfo": {"count": 120}},
        "armsInterventionsModule": {"interventions": [{"type": t, "name": n, "description": d} for t, n, d in interventions]},
        "descriptionModule": {"briefSummary": summary},
        "eligibilityModule": {"minimumAge": "8 Years"},
        "contactsLocationsModule": {"locations": [{"facility": f"{c} Eye Center", "city": "City", "country": c, "status": "RECRUITING"} for c in countries]},
    }}

raw = [
    study("NCT00000001", "Gene Therapy for X-linked RP Caused by RPGR Mutations", "ACTIVE_NOT_RECRUITING", ["PHASE3"],
          "Beacon Therapeutics", [("GENETIC", "AAV8-RPGR (laruparetigene zovaparvovec)", "subretinal")], ["United States", "United Kingdom", "Germany"],
          cond=("X-Linked Retinitis Pigmentosa",)),
    study("NCT00000002", "Optogenetic Therapy MCO-010 in Advanced RP", "RECRUITING", ["PHASE2"], "Nanoscope Therapeutics",
          [("GENETIC", "MCO-010", "intravitreal")], ["United States"]),
    study("NCT00000003", "Oral N-acetylcysteine for RP (NAC Attack)", "RECRUITING", ["PHASE3"], "Johns Hopkins University",
          [("DRUG", "N-acetylcysteine", "oral tablet")], ["United States", "Canada", "France", "Netherlands", "Spain", "Italy"]),
    study("NCT00000004", "OCU400 Modifier Gene Therapy for RP", "NOT_YET_RECRUITING", ["PHASE3"], "Ocugen",
          [("GENETIC", "OCU400", "AAV-NR2E3 subretinal, gene-agnostic")], ["United States"]),
    study("NCT00000005", "Natural History of USH2A-related Retinal Degeneration", "COMPLETED", [], "Foundation Fighting Blindness",
          [], ["United States", "Belgium"], cond=("Usher Syndrome Type 2",), study_type="OBSERVATIONAL"),
    study("NCT00000006", "jCell Intravitreal Retinal Progenitor Cells", "ACTIVE_NOT_RECRUITING", ["PHASE2"], "jCyte",
          [("BIOLOGICAL", "jCell", "retinal progenitor cells")], ["United States"]),
    study("NCT00000007", "Antisense oligonucleotide ultevursen for USH2A exon 13", "TERMINATED", ["PHASE2", "PHASE3"], "Laboratoires Thea",
          [("DRUG", "Ultevursen", "antisense")], ["United States", "Netherlands"], cond=("Usher Syndrome", "Retinitis Pigmentosa")),
]
papers = [
    {"uid": "41000001", "title": "Long-term outcomes of <i>RPGR</i> gene therapy in XLRP: a phase 1/2 trial", "fulljournalname": "Ophthalmology",
     "pubdate": "2026 Nov", "sortpubdate": "2026/11/02 00:00", "authors": [{"name": "Smith J"}, {"name": "Lee K"}, {"name": "Patel R"}, {"name": "Garcia M"}],
     "articleids": [{"idtype": "doi", "value": "10.1/x"}], "pubtype": ["Journal Article", "Clinical Trial, Phase II"]},
    {"uid": "41000002", "title": "Rod rescue by CRISPR base editing in Pde6b mice", "fulljournalname": "Nature Communications",
     "pubdate": "2026 Oct 30", "sortpubdate": "2026/10/30 00:00", "authors": [{"name": "Chen Y"}], "articleids": [], "pubtype": ["Journal Article"]},
    {"uid": "41000003", "title": "Retinitis pigmentosa therapies: a review", "fulljournalname": "Prog Retin Eye Res",
     "pubdate": "2026 Oct", "sortpubdate": "2026/10/15 00:00", "authors": [{"name": "Doe A"}], "articleids": [], "pubtype": ["Review"]},
]
norm_p = [fetcher.normalize_paper(p) for p in papers]

trials1 = [fetcher.normalize_trial(s) for s in raw]
for t in trials1:
    print(t["nct_id"], t["approaches"], t["genes"], "agnostic" if t["gene_agnostic"] else "", t["phase_label"])
assert trials1[0]["approaches"] == ["Gene therapy"] and trials1[0]["genes"] == ["RPGR"]
assert trials1[1]["approaches"] == ["Optogenetics"] and trials1[1]["gene_agnostic"]
assert trials1[2]["approaches"] == ["Drug / neuroprotection"]
assert trials1[4]["approaches"] == ["Natural history / observational"]
assert trials1[5]["approaches"] == ["Cell therapy"]
assert trials1[6]["approaches"] == ["RNA / antisense"]
assert [p["kind"] for p in norm_p] == ["Human trial", "Lab / animal", "Review"], [p["kind"] for p in norm_p]
assert norm_p[0]["title"].startswith("Long-term outcomes of RPGR")

r1 = refresh.run(trials=trials1, papers=norm_p[2:])
assert store.get_run(r1)["summary"]["baseline"]

# week 2: OCU400 starts recruiting, jCell posts results, a new trial, record update, new papers
raw2 = copy.deepcopy(raw)
raw2[3]["protocolSection"]["statusModule"]["overallStatus"] = "RECRUITING"
raw2[5]["hasResults"] = True
raw2[0]["protocolSection"]["statusModule"]["lastUpdatePostDateStruct"]["date"] = "2026-11-05"
raw2.append(study("NCT00000008", "Subretinal Gene Therapy for RHO-adRP", "NOT_YET_RECRUITING", ["PHASE1", "PHASE2"], "Example Bio",
                  [("GENETIC", "AAV-RHO knockdown/replace", "subretinal")], ["Australia", "Japan"], cond=("Autosomal Dominant Retinitis Pigmentosa",)))
trials2 = [fetcher.normalize_trial(s) for s in raw2]
r2 = refresh.run(trials=trials2, papers=norm_p)
s2 = store.get_run(r2)["summary"]
print({k: v for k, v in s2.items() if k != "ai_summary"})
assert s2["new_trials"] == ["NCT00000008"]
assert s2["status_changes"] == [{"nct_id": "NCT00000004", "from": "NOT_YET_RECRUITING", "to": "RECRUITING"}]
assert s2["new_results"] == ["NCT00000006"]
assert s2["updated"] == ["NCT00000001"]
assert sorted(s2["new_papers"]) == ["41000001", "41000002"]

import build
# persistence: reload from disk and confirm the diff state survived
store.reset(); assert store.get_run(r2)["summary"]["new_results"] == ["NCT00000006"]
assert r2 == r1 + "-2", (r1, r2)

note = os.path.join(build.HERE, "content", "note.md")
orig = open(note).read()
open(note, "w").write(orig + "# Why this week matters\nOCU400's Phase 3 is **now enrolling**. Ask your specialist.\n")
try:
    out = os.path.join(TMP, "site"); build.build(out)
finally:
    open(note, "w").write(orig)

def page(rel):
    return open(os.path.join(out, rel), encoding="utf-8").read()

for rel in ["index.html", f"week/{r1}.html", f"week/{r2}.html", "trials.html", "trial/NCT00000003.html",
            "pipeline.html", "papers.html", "about.html", "404.html", "feed.xml", ".nojekyll", "static/style.css"]:
    assert os.path.exists(os.path.join(out, rel)), rel
assert len(os.listdir(os.path.join(out, "trial"))) == 8
home = page("index.html")
assert "This week: 1 trial started recruiting, 1 trial posted results, 1 new trial registered, 2 new papers." in home
assert "now enrolling" in home and 'href="trial/NCT00000004.html"' in home and 'href="static/style.css"' in home
assert "now enrolling" not in page(f"week/{r1}.html"), "note should only show on the latest week"
assert "Tracking 5 active RP trials" in page(f"week/{r1}.html")
tp = page("trial/NCT00000004.html"); assert 'href="../static/style.css"' in tp and "Not gene-specific (mentions NR2E3)" in tp
tr = page("trials.html"); assert tr.count('data-status=') == 8 and 'data-gene="RPGR"' in tr and "_agnostic" in tr
assert "https://example.org/rp/static/style.css" in page("404.html")
feed = page("feed.xml"); assert "<link>https://example.org/rp/week/" in feed and "Week of 2026" in feed
print("ALL TESTS PASSED")
print("SITE_DIR", out)
