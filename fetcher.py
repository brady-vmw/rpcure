"""Pull RP research data from public sources and normalize it.

Sources
- ClinicalTrials.gov API v2 (covers US + most industry trials run worldwide)
- PubMed E-utilities (new peer-reviewed papers)
"""
import os
import re
import time

import requests

USER_AGENT = "rp-research-tracker/1.0 (+https://rpcure.com)"
CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Conditions tracked. RP plus the closely related forms families usually care about.
CONDITIONS = [
    "retinitis pigmentosa",
    "Usher syndrome",
    "rod-cone dystrophy",
    "Leber congenital amaurosis",
    "inherited retinal degeneration",
    "inherited retinal dystrophy",
    "X-linked retinitis pigmentosa",
    "Bardet-Biedl syndrome",
]

PUBMED_TERM = (
    '("retinitis pigmentosa"[tiab] OR "Retinitis Pigmentosa"[mh] OR "Usher syndrome"[tiab] '
    'OR "rod-cone dystrophy"[tiab] OR "Leber congenital amaurosis"[tiab] '
    'OR "inherited retinal degeneration"[tiab] OR "inherited retinal dystrophy"[tiab] '
    'OR "inherited retinal disease"[tiab] OR "inherited retinal diseases"[tiab])'
)

STATUS_LABELS = {
    "RECRUITING": "Recruiting now",
    "NOT_YET_RECRUITING": "Not yet recruiting",
    "ENROLLING_BY_INVITATION": "Enrolling by invitation",
    "ACTIVE_NOT_RECRUITING": "Underway, not enrolling",
    "COMPLETED": "Completed",
    "SUSPENDED": "Suspended",
    "TERMINATED": "Terminated",
    "WITHDRAWN": "Withdrawn",
    "UNKNOWN": "Status unknown",
    "AVAILABLE": "Expanded access available",
    "NO_LONGER_AVAILABLE": "Expanded access ended",
    "TEMPORARILY_NOT_AVAILABLE": "Expanded access paused",
    "APPROVED_FOR_MARKETING": "Approved for marketing",
    "WITHHELD": "Withheld",
}
ACTIVE_STATUSES = {"RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION",
                   "ACTIVE_NOT_RECRUITING", "AVAILABLE"}

PHASE_LABELS = {
    "EARLY_PHASE1": "Early Phase 1", "PHASE1": "Phase 1", "PHASE2": "Phase 2",
    "PHASE3": "Phase 3", "PHASE4": "Phase 4", "NA": "N/A",
}
PHASE_RANK = {"EARLY_PHASE1": 0, "PHASE1": 1, "PHASE2": 2, "PHASE3": 3, "PHASE4": 4}

# Ordered: first match wins for the "primary" approach, but all matches are kept.
APPROACH_RULES = [
    ("Gene editing", r"crispr|gene[- ]edit|base[- ]edit|prime[- ]edit|edit-101"),
    ("Optogenetics", r"optogenetic|channelrhodopsin|mco-010|sonpiretigene|gs030|rst-001|kio-301"),
    ("Gene therapy", r"\baav|raav|gene therapy|gene transfer|subretinal|vector|retigene|ocu400|4d-125|ocugen"),
    ("RNA / antisense", r"antisense|oligonucleotide|\baon\b|sepofarsen|ultevursen|\bsirna|\bmrna"),
    ("Cell therapy", r"stem cell|progenitor|cell therapy|jcell|ipsc|\besc\b|photoreceptor precursor|cell transplant|bone marrow"),
    ("Retinal implant", r"retinal implant|retinal prosthes|visual prosthes|argus|\bprima\b|bionic|photovoltaic"),
    ("Electrical stimulation", r"electrical stimulation|transcorneal|okustim|microcurrent|\btes\b"),
    ("Supplement / diet", r"vitamin|omega|\bdha\b|lutein|supplement|diet"),
    ("Drug / neuroprotection", r"acetylcysteine|\bnac\b|valpro|neuroprotect|tablet|capsule|\boral\b|eye drop|intravitreal|\bdrug\b"),
]

GENES = ["RPGR", "RHO", "USH2A", "USH1C", "MYO7A", "CDH23", "PCDH15", "CLRN1", "ADGRV1",
         "PDE6A", "PDE6B", "CNGA1", "CNGB1", "RLBP1", "MERTK", "RPE65", "CEP290", "PRPF31",
         "PRPF8", "EYS", "NR2E3", "CRB1", "BEST1", "MAK", "RP1", "RP2", "CHM", "CRX",
         "GUCY2D", "LCA5", "RDH12", "AIPL1", "TULP1", "SNRNP200", "PRPH2", "BBS1", "BBS10",
         "IMPDH1", "NRL"]
GENE_RE = re.compile(r"\b(" + "|".join(GENES) + r")\b")
GENE_AGNOSTIC_RE = re.compile(
    r"gene[- ]agnostic|mutation[- ]agnostic|regardless of (genetic|gene|mutation)|optogenetic|"
    r"acetylcysteine|\bnac\b|ocu400|mco-010|sonpiretigene|jcell", re.I)


def _get(url, params, tries=3):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=60, headers={"User-Agent": USER_AGENT})
            if r.status_code == 429:
                time.sleep(2 + 3 * i)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = e
            time.sleep(2 + 3 * i)
    raise RuntimeError(f"Request failed: {url} ({last})")


# ---------------- ClinicalTrials.gov ----------------

def ctgov_condition_query():
    return " OR ".join(f'"{c}"' for c in CONDITIONS)


def fetch_trials():
    """Return every study (any status) matching the tracked conditions."""
    studies, token = [], None
    while True:
        params = {
            "query.cond": ctgov_condition_query(),
            "pageSize": 1000,
            "format": "json",
            "fields": "protocolSection,hasResults",
        }
        if token:
            params["pageToken"] = token
        data = _get(CTGOV_URL, params)
        studies.extend(data.get("studies", []))
        token = data.get("nextPageToken")
        if not token:
            break
    return [normalize_trial(s) for s in studies]


def _date(struct):
    return (struct or {}).get("date") or ""


def classify(text, interventions, study_type):
    t = text.lower()
    approaches = [name for name, rx in APPROACH_RULES if re.search(rx, t)]
    advanced = {"Gene editing", "Optogenetics", "Gene therapy", "RNA / antisense", "Cell therapy",
                "Retinal implant", "Electrical stimulation"}
    if any(a in advanced for a in approaches):
        approaches = [a for a in approaches if a in advanced]
    if not approaches:
        if study_type == "OBSERVATIONAL":
            approaches = ["Natural history / observational"]
        elif any(i.get("type") == "DEVICE" for i in interventions):
            approaches = ["Device"]
        elif any(i.get("type") in ("DRUG", "BIOLOGICAL") for i in interventions):
            approaches = ["Drug / neuroprotection"]
        else:
            approaches = ["Other"]
    if study_type == "OBSERVATIONAL" and "Natural history / observational" not in approaches:
        approaches = ["Natural history / observational"]
    return approaches


def normalize_trial(s):
    ps = s.get("protocolSection", {})
    ident = ps.get("identificationModule", {})
    stat = ps.get("statusModule", {})
    spons = ps.get("sponsorCollaboratorsModule", {})
    cond = ps.get("conditionsModule", {})
    design = ps.get("designModule", {})
    arms = ps.get("armsInterventionsModule", {})
    desc = ps.get("descriptionModule", {})
    locs = ps.get("contactsLocationsModule", {}).get("locations", []) or []
    elig = ps.get("eligibilityModule", {})

    interventions = [
        {"type": i.get("type", ""), "name": i.get("name", ""), "description": i.get("description", "")}
        for i in (arms.get("interventions") or [])
    ]
    phases = design.get("phases") or []
    study_type = design.get("studyType", "")
    conditions = cond.get("conditions") or []
    title = ident.get("briefTitle", "")
    summary = desc.get("briefSummary", "")

    blob = " ".join([title, ident.get("officialTitle", ""), summary, " ".join(conditions),
                     " ".join(cond.get("keywords") or []),
                     " ".join(i["name"] + " " + i["description"] for i in interventions)])
    genes = sorted(set(GENE_RE.findall(blob)))
    gene_agnostic = bool(GENE_AGNOSTIC_RE.search(blob))

    countries = sorted({l.get("country") for l in locs if l.get("country")})
    top_phase = max((PHASE_RANK.get(p, -1) for p in phases), default=-1)

    return {
        "nct_id": ident.get("nctId"),
        "title": title,
        "official_title": ident.get("officialTitle", ""),
        "acronym": ident.get("acronym", ""),
        "status": stat.get("overallStatus", "UNKNOWN"),
        "status_label": STATUS_LABELS.get(stat.get("overallStatus", ""), stat.get("overallStatus", "")),
        "active": stat.get("overallStatus") in ACTIVE_STATUSES,
        "why_stopped": stat.get("whyStopped", ""),
        "start_date": _date(stat.get("startDateStruct")),
        "primary_completion": _date(stat.get("primaryCompletionDateStruct")),
        "completion": _date(stat.get("completionDateStruct")),
        "first_posted": _date(stat.get("studyFirstPostDateStruct")),
        "last_update": _date(stat.get("lastUpdatePostDateStruct")),
        "sponsor": (spons.get("leadSponsor") or {}).get("name", ""),
        "sponsor_class": (spons.get("leadSponsor") or {}).get("class", ""),
        "conditions": conditions,
        "study_type": study_type,
        "phases": phases,
        "phase_label": " / ".join(PHASE_LABELS.get(p, p) for p in phases) or
                       ("Observational" if study_type == "OBSERVATIONAL" else "N/A"),
        "phase_rank": top_phase,
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "interventions": interventions,
        "approaches": classify(blob, interventions, study_type),
        "genes": genes,
        "gene_agnostic": gene_agnostic,
        "summary": summary,
        "min_age": elig.get("minimumAge", ""),
        "max_age": elig.get("maximumAge", ""),
        "countries": countries,
        "locations": [
            {"facility": l.get("facility", ""), "city": l.get("city", ""),
             "state": l.get("state", ""), "country": l.get("country", ""),
             "status": l.get("status", "")}
            for l in locs
        ],
        "has_results": bool(s.get("hasResults")),
        "url": f"https://clinicaltrials.gov/study/{ident.get('nctId')}",
    }


# ---------------- PubMed ----------------

def _eutils_params(extra):
    p = {"db": "pubmed", "retmode": "json", "tool": "rp-research-tracker"}
    if os.environ.get("NCBI_EMAIL"):
        p["email"] = os.environ["NCBI_EMAIL"]
    if os.environ.get("NCBI_API_KEY"):
        p["api_key"] = os.environ["NCBI_API_KEY"]
    p.update(extra)
    return p


def fetch_papers(days=8):
    """Papers added to PubMed in the last `days` days."""
    search = _get(f"{EUTILS}/esearch.fcgi", _eutils_params({
        "term": PUBMED_TERM, "reldate": days, "datetype": "edat", "retmax": 500, "sort": "pub_date",
    }))
    ids = search.get("esearchresult", {}).get("idlist", [])
    papers = []
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        summ = _get(f"{EUTILS}/esummary.fcgi", _eutils_params({"id": ",".join(chunk)}))
        result = summ.get("result", {})
        for uid in result.get("uids", []):
            papers.append(normalize_paper(result[uid]))
        time.sleep(0.4)
    return papers


def paper_kind(pubtypes, title):
    pt = {p.lower() for p in pubtypes}
    t = title.lower()
    if pt & {"clinical trial", "randomized controlled trial", "clinical trial, phase i",
             "clinical trial, phase ii", "clinical trial, phase iii", "controlled clinical trial"}:
        return "Human trial"
    if pt & {"review", "systematic review", "meta-analysis"}:
        return "Review"
    if "case reports" in pt:
        return "Case report"
    if re.search(r"\bmice\b|\bmouse\b|\brat\b|\brats\b|zebrafish|\bdog\b|canine|porcine|\bpig\b|organoid|in vitro|cell line", t):
        return "Lab / animal"
    return "Research"


def normalize_paper(r):
    doi = next((a.get("value") for a in r.get("articleids", []) if a.get("idtype") == "doi"), "")
    authors = [a.get("name", "") for a in r.get("authors", [])]
    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    title = re.sub(r"<[^>]+>", "", r.get("title", "")).strip()
    return {
        "pmid": str(r.get("uid")),
        "title": title,
        "journal": r.get("fulljournalname") or r.get("source", ""),
        "pub_date": r.get("pubdate", ""),
        "sort_date": (r.get("sortpubdate") or "")[:10].replace("/", "-"),
        "authors": author_str,
        "doi": doi,
        "kind": paper_kind(r.get("pubtype", []), title),
        "genes": sorted(set(GENE_RE.findall(title))),
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{r.get('uid')}/",
    }
