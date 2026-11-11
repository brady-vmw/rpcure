"""Pull RP research data from public sources, filter out off-topic records, and normalize.

Sources
- ClinicalTrials.gov API v2 (covers US + most industry trials run worldwide)
- PubMed E-utilities (new papers, with abstracts)
"""
import os
import re
import time
import xml.etree.ElementTree as ET

import requests

USER_AGENT = "rp-research-tracker/1.0 (+https://rpcure.com)"
CTGOV_URL = "https://clinicaltrials.gov/api/v2/studies"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# Registry search terms (broad on purpose; is_relevant_trial does the real filtering).
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

# ---------- relevance ----------

# A trial is in scope only if its conditions, keywords or title name a form of RP / inherited retinal disease.
RP_CORE = re.compile(
    r"retinitis pigmentosa|pigmentary retinopathy|rod[- ]cone dystroph|usher|leber congenital|"
    r"congenital amaurosis|\blca\d*\b|x[- ]linked rp|\bxlrp\b|inherited retinal|"
    r"retinal dystroph|retinal degenerat|hereditary retinal|\bird\b|\birds\b|"
    r"retinal disease|retinopath", re.I)
EYE_TERMS = re.compile(r"retin|vision|visual|photoreceptor|\beye\b|ocular|ophthalm|blind", re.I)
OFF_TOPIC = re.compile(r"melanoma|obesity|hyperphagia|arthritis|kidney|renal fibro|polycystic|"
                       r"mitochondrial disease|neurodegenerative disorders|neurological disease|prader", re.I)
STARGARDT_ONLY = re.compile(r"stargardt|abca4|cone[- ]rod|macular degeneration|achromatopsia", re.I)


def is_relevant_trial(t):
    conds = " ".join(t.get("conditions", []) + t.get("keywords", []))
    title = t.get("title", "") + " " + t.get("official_title", "")
    head = conds + " " + title
    if not RP_CORE.search(head):
        return False
    if OFF_TOPIC.search(title) and not EYE_TERMS.search(title):
        return False
    # syndromic (Bardet-Biedl, Alstrom) records count only when they are about the retina
    if re.search(r"bardet|alstr", conds, re.I) and not re.search(
            r"retinitis|retinal|retinopathy|rod[- ]cone|vision", title, re.I):
        return False
    # Stargardt / cone-rod / AMD trials matched only through a generic "retinopathy" tag are out of scope
    rp_specific = re.search(r"retinitis pigmentosa|rod[- ]cone|usher|leber|amaurosis|\bxlrp\b|"
                            r"inherited retinal|retinal dystroph|retinal degenerat|choroideremia",
                            " ".join(t.get("conditions", [])) + " " + t.get("title", ""), re.I)
    if STARGARDT_ONLY.search(head) and not rp_specific:
        return False
    return True


# ---------- classification ----------

# Ordered: first match wins for the "primary" approach.
APPROACH_RULES = [
    ("Gene editing", r"crispr|gene[- ]edit|base[- ]edit|prime[- ]edit|edit-101"),
    ("Optogenetics", r"optogenetic|channelrhodopsin|mco-010|sonpiretigene|gs030|rst-001|rtx-015|ray therapeutics"),
    ("Photoswitch", r"kio-301|photoswitch"),
    ("Cell therapy", r"stem cell|progenitor|cell therapy|jcell|famzeretcel|ipsc|\besc\b|photoreceptor precursor|"
                     r"cell transplant|retinal transplant|transplantation of|bone marrow|mesenchymal|uc-msc"),
    ("Gene therapy", r"\baav|raav|gene therapy|gene transfer|gene replacement|retigene|parvovec|ocu400|4d-125|lx101"),
    ("RNA / antisense", r"antisense|oligonucleotide|\baon\b|sepofarsen|ultevursen|\bsirna|\bmrna"),
    ("Retinal implant", r"retinal implant|retinal prosthes|visual prosthes|argus|\bprima\b|bionic|photovoltaic"),
    ("Electrical stimulation", r"electrical stimulation|transcorneal|okustim|microcurrent|\btes\b"),
    ("Supplement / diet", r"vitamin|omega|\bdha\b|lutein|supplement|diet"),
    ("Drug / neuroprotection", r"acetylcysteine|\bnac\b|npi-001|valpro|neuroprotect|tablet|capsule|\boral\b|"
                               r"eye drop|intravitreal"),
]
ADVANCED = {"Gene editing", "Optogenetics", "Photoswitch", "Cell therapy", "Gene therapy", "RNA / antisense",
            "Retinal implant", "Electrical stimulation"}

GENES = ["RPGR", "RHO", "USH2A", "USH1C", "MYO7A", "CDH23", "PCDH15", "CLRN1", "ADGRV1",
         "PDE6A", "PDE6B", "CNGA1", "CNGB1", "RLBP1", "MERTK", "RPE65", "CEP290", "PRPF31",
         "PRPF8", "EYS", "NR2E3", "CRB1", "BEST1", "MAK", "RP1", "RP2", "CHM", "CRX",
         "GUCY2D", "LCA5", "RDH12", "AIPL1", "TULP1", "SNRNP200", "PRPH2", "BBS1", "BBS10",
         "IMPDH1", "NRL", "CFAP418", "ALMS1", "ABCA4"]
GENE_RE = re.compile(r"\b(" + "|".join(GENES) + r")\b")
GENE_AGNOSTIC_RE = re.compile(
    r"gene[- ]agnostic|mutation[- ]agnostic|independent of (the )?(causative )?(gene|mutation)|"
    r"regardless of (genetic|gene|mutation)|optogenetic|photoswitch|kio-301|"
    r"acetylcysteine|\bnac\b|npi-001|ocu400|mco-010|sonpiretigene|rtx-015|ray therapeutics|jcell|famzeretcel", re.I)

# "Interventions" that are not the treatment being tested.
NOT_A_TREATMENT = re.compile(r"placebo|sham|no intervention|no treatment|untreated|vehicle|"
                             r"standard of care|blood draw|observation|\bcontrol\b|saline", re.I)


def _get(url, params, tries=3, as_text=False):
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=60, headers={"User-Agent": USER_AGENT})
            if r.status_code == 429:
                time.sleep(2 + 3 * i)
                continue
            r.raise_for_status()
            return r.text if as_text else r.json()
        except requests.RequestException as e:
            last = e
            time.sleep(2 + 3 * i)
    raise RuntimeError(f"Request failed: {url} ({last})")


# ---------------- ClinicalTrials.gov ----------------

def ctgov_condition_query():
    return " OR ".join(f'"{c}"' for c in CONDITIONS)


def fetch_trials():
    """Every in-scope study (any status) matching the tracked conditions."""
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
    trials = [normalize_trial(s) for s in studies]
    return [t for t in trials if t["nct_id"] and is_relevant_trial(t)]


def _date(struct):
    return (struct or {}).get("date") or ""


def classify(text, interventions, study_type):
    t = text.lower()
    approaches = [name for name, rx in APPROACH_RULES if re.search(rx, t)]
    if any(a in ADVANCED for a in approaches):
        approaches = [a for a in approaches if a in ADVANCED]
    if not approaches:
        if any(i.get("type") == "DEVICE" for i in interventions):
            approaches = ["Device"]
        elif any(i.get("type") in ("DRUG", "BIOLOGICAL") for i in interventions):
            approaches = ["Drug / neuroprotection"]
        else:
            approaches = ["Other"]
    if re.search(r"natural history|\bcohort\b|\bregistry\b|prescreen", t) and not any(
            i.get("type") in ("DRUG", "BIOLOGICAL", "GENETIC") for i in interventions):
        approaches = ["Natural history / observational"]
    if study_type == "OBSERVATIONAL":
        approaches = ["Natural history / observational"]
    return approaches


def lead_intervention(t):
    """Name of the treatment actually being tested (skips placebo, sham, etc.)."""
    real = [i for i in t.get("interventions", []) if not NOT_A_TREATMENT.search(i.get("name", ""))]
    if not real:
        return t.get("acronym") or t.get("title", "")
    name = real[0]["name"]
    short = re.sub(r"\s*\(.*?\)", "", name).strip()          # "AGTC-501 (high dose ...)" -> "AGTC-501"
    short = re.sub(r"^\d+(\.\d+)?\s*(µg|μg|mg|ug|m)\s+", "", short, flags=re.I)  # "100 μg KIO-301" -> "KIO-301"
    return short or name


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
    t = {
        "nct_id": ident.get("nctId"),
        "title": ident.get("briefTitle", ""),
        "official_title": ident.get("officialTitle", ""),
        "acronym": ident.get("acronym", ""),
        "status": stat.get("overallStatus", "UNKNOWN"),
        "why_stopped": stat.get("whyStopped", ""),
        "start_date": _date(stat.get("startDateStruct")),
        "primary_completion": _date(stat.get("primaryCompletionDateStruct")),
        "completion": _date(stat.get("completionDateStruct")),
        "first_posted": _date(stat.get("studyFirstPostDateStruct")),
        "last_update": _date(stat.get("lastUpdatePostDateStruct")),
        "sponsor": (spons.get("leadSponsor") or {}).get("name", ""),
        "sponsor_class": (spons.get("leadSponsor") or {}).get("class", ""),
        "conditions": cond.get("conditions") or [],
        "keywords": cond.get("keywords") or [],
        "study_type": design.get("studyType", ""),
        "phases": design.get("phases") or [],
        "enrollment": (design.get("enrollmentInfo") or {}).get("count"),
        "interventions": interventions,
        "summary": desc.get("briefSummary", ""),
        "min_age": elig.get("minimumAge", ""),
        "max_age": elig.get("maximumAge", ""),
        "sex": elig.get("sex", ""),
        "healthy_volunteers": elig.get("healthyVolunteers"),
        "eligibility_text": elig.get("eligibilityCriteria", ""),
        "locations": [
            {"facility": l.get("facility", ""), "city": l.get("city", ""),
             "state": l.get("state", ""), "country": l.get("country", ""),
             "status": l.get("status", "")}
            for l in locs
        ],
        "has_results": bool(s.get("hasResults")),
        "url": f"https://clinicaltrials.gov/study/{ident.get('nctId')}",
    }
    return derive(t)


def _age_years(s):
    m = re.match(r"(\d+)\s*(year|month|week|day)", (s or "").lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    return n if unit == "year" else round(n / {"month": 12, "week": 52, "day": 365}[unit], 2)


def derive(t):
    """Fields computed from the raw record. Re-run on every build so rule changes apply to stored trials."""
    seen, conditions = set(), []
    for c in t.get("conditions", []):
        k = re.sub(r"[^a-z0-9]", "", c.lower())
        if k and k not in seen:
            seen.add(k)
            conditions.append(c)
    t["conditions"] = conditions
    blob = " ".join([t.get("title", ""), t.get("official_title", ""), t.get("summary", ""),
                     " ".join(conditions), " ".join(t.get("keywords", [])),
                     " ".join(i["name"] + " " + i["description"] for i in t.get("interventions", []))])
    phases = t.get("phases", [])
    t["phase_rank"] = max((PHASE_RANK.get(p, -1) for p in phases), default=-1)
    t["status_label"] = STATUS_LABELS.get(t["status"], t["status"])
    t["active"] = t["status"] in ACTIVE_STATUSES
    t["phase_label"] = (" / ".join(PHASE_LABELS.get(p, p) for p in phases) or
                        ("Observational" if t.get("study_type") == "OBSERVATIONAL" else "N/A"))
    t["approaches"] = classify(blob, t.get("interventions", []), t.get("study_type", ""))
    t["genes"] = sorted(set(GENE_RE.findall(blob)))
    t["gene_agnostic"] = bool(GENE_AGNOSTIC_RE.search(blob))
    t["lead"] = lead_intervention(t)
    t["countries"] = sorted({l["country"] for l in t.get("locations", []) if l.get("country")})
    t["min_age_years"] = _age_years(t.get("min_age"))
    t["max_age_years"] = _age_years(t.get("max_age"))
    t["us_states"] = sorted({l["state"] for l in t.get("locations", [])
                             if l.get("country") == "United States" and l.get("state")})
    t["is_treatment"] = t.get("study_type") == "INTERVENTIONAL" and \
        t["approaches"][0] != "Natural history / observational"
    return t


def reclassify(t):
    return derive(t)


# ---------------- PubMed ----------------

def _eutils_params(extra):
    p = {"db": "pubmed", "tool": "rp-research-tracker"}
    if os.environ.get("NCBI_EMAIL"):
        p["email"] = os.environ["NCBI_EMAIL"]
    if os.environ.get("NCBI_API_KEY"):
        p["api_key"] = os.environ["NCBI_API_KEY"]
    p.update(extra)
    return p


def fetch_abstracts(pmids):
    out = {}
    for i in range(0, len(pmids), 150):
        chunk = pmids[i:i + 150]
        xml = _get(f"{EUTILS}/efetch.fcgi", _eutils_params({"id": ",".join(chunk), "retmode": "xml"}),
                   as_text=True)
        root = ET.fromstring(xml)
        for art in root.iter("PubmedArticle"):
            pmid = art.findtext(".//MedlineCitation/PMID")
            parts = []
            for a in art.iter("AbstractText"):
                txt = "".join(a.itertext()).strip()
                label = a.get("Label")
                parts.append(f"{label}: {txt}" if label else txt)
            out[pmid] = " ".join(parts)
        time.sleep(0.4)
    return out


def fetch_papers(days=8):
    """In-scope papers added to PubMed in the last `days` days."""
    search = _get(f"{EUTILS}/esearch.fcgi", _eutils_params({
        "term": PUBMED_TERM, "reldate": days, "datetype": "edat", "retmax": 500,
        "sort": "pub_date", "retmode": "json",
    }))
    ids = search.get("esearchresult", {}).get("idlist", [])
    papers = []
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        summ = _get(f"{EUTILS}/esummary.fcgi", _eutils_params({"id": ",".join(chunk), "retmode": "json"}))
        result = summ.get("result", {})
        for uid in result.get("uids", []):
            papers.append(normalize_paper(result[uid]))
        time.sleep(0.4)
    abstracts = fetch_abstracts([p["pmid"] for p in papers]) if papers else {}
    for p in papers:
        p["abstract"] = abstracts.get(p["pmid"], "")
    return [p for p in papers if is_relevant_paper(p)]


RETINA_WORD = re.compile(
    r"retin|photoreceptor|\brods?\b|\bcones?\b|vision|visual|\beyes?\b|ocular|ophthalm|macul|usher|leber|"
    r"amaurosis|blind|optogenet|pigmentosa|choroid|fovea|rhodopsin|bardet|electroretino|ciliopath|perimetry", re.I)
EYE_JOURNAL = re.compile(r"ophthalm|retina|vision|visual|\beye\b|ocular|optom", re.I)
RP_CORE_PAPER = re.compile(r"retinitis pigmentosa|inherited retinal|retinal degenerat|retinal dystroph|"
                           r"usher syndrome|leber congenital|rod[- ]cone|photoreceptor degenerat", re.I)


def is_relevant_paper(p):
    """Drop papers that only mention RP in passing (yeast splicing, birdsong, etc.)."""
    if RETINA_WORD.search(p["title"]) or EYE_JOURNAL.search(p.get("journal", "")):
        return True
    if GENE_RE.search(p["title"]):
        return True
    return len(RP_CORE_PAPER.findall(p.get("abstract", ""))) >= 2


def paper_kind(pubtypes, title, journal):
    pt = {x.lower() for x in pubtypes}
    t = title.lower()
    if "preprint" in pt or re.search(r"rxiv", journal or "", re.I):
        return "Preprint (not yet peer-reviewed)"
    if pt & {"news", "newspaper article", "editorial", "comment", "interview"}:
        return "News or commentary"
    if pt & {"clinical trial", "randomized controlled trial", "clinical trial, phase i",
             "clinical trial, phase ii", "clinical trial, phase iii", "controlled clinical trial"}:
        return "Human trial results"
    if pt & {"review", "systematic review", "meta-analysis"}:
        return "Review"
    if "case reports" in pt or re.search(r"case (report|series)", t):
        return "Case report"
    if re.search(r"\bmice\b|\bmouse\b|\brat\b|\brats\b|zebrafish|\bdog\b|canine|porcine|\bpig\b|organoid|"
                 r"in vitro|cell line|murine|drosophila|\byeast\b", t):
        return "Lab or animal study"
    return "Research"


def normalize_paper(r):
    doi = next((a.get("value") for a in r.get("articleids", []) if a.get("idtype") == "doi"), "")
    authors = [a.get("name", "") for a in r.get("authors", [])]
    author_str = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
    title = re.sub(r"<[^>]+>", "", r.get("title", "")).strip()
    journal = r.get("fulljournalname") or r.get("source", "")
    pubtypes = r.get("pubtype", [])
    entrez = next((h.get("date", "") for h in r.get("history", []) if h.get("pubstatus") == "entrez"), "")
    return {
        "pmid": str(r.get("uid")),
        "title": title,
        "journal": journal,
        "pub_date": r.get("pubdate", ""),
        "epub_date": r.get("epubdate", ""),
        "added_date": entrez[:10].replace("/", "-"),
        "sort_date": (r.get("sortpubdate") or "")[:10].replace("/", "-"),
        "authors": author_str,
        "doi": doi,
        "pubtypes": pubtypes,
        "kind": paper_kind(pubtypes, title, journal),
        "genes": sorted(set(GENE_RE.findall(title))),
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{r.get('uid')}/",
    }
