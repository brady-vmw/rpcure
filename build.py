"""Build the static site.

  python build.py           render site/ from data/state.json and content/*.json (no network)
  python build.py --fetch   pull fresh data first (the weekly GitHub Action does this)

Env: SITE_URL (absolute base for the RSS feed), SITE_NAME, ANTHROPIC_API_KEY, NCBI_EMAIL, NCBI_API_KEY.
"""
import argparse
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime

import markdown as md
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

import fetcher
import refresh
import store

HERE = os.path.dirname(os.path.abspath(__file__))
SITE_NAME = os.environ.get("SITE_NAME", "RP Cure Tracker")
PHASE_COLUMNS = [("Early Phase 1 / Phase 1", {0, 1}), ("Phase 2", {2}), ("Phase 3", {3}), ("Phase 4", {4})]
STATUS_RANK = {"RECRUITING": 0, "NOT_YET_RECRUITING": 1, "ENROLLING_BY_INVITATION": 2, "ACTIVE_NOT_RECRUITING": 3}
PAPER_KIND_ORDER = {"Human trial results": 0, "Research": 1, "Review": 2, "Lab or animal study": 3,
                    "Case report": 4, "Preprint (not yet peer-reviewed)": 5, "News or commentary": 6}
UPDATE_WEEKDAY = 4  # Friday
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


# ---------------- helpers ----------------

def load_json(name, default):
    path = os.path.join(HERE, "content", name)
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_date(s):
    s = (s or "").strip()
    for fmt, n in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            return datetime.strptime(s[:n], fmt).date(), fmt
        except ValueError:
            continue
    return None, None


def nice_date(s, weekday=False):
    """'2026-11-13' -> 'November 13, 2026'; '2027-06' -> 'June 2027'; leaves free text alone."""
    if isinstance(s, (date, datetime)):
        d, fmt = (s.date() if isinstance(s, datetime) else s), "%Y-%m-%d"
    else:
        d, fmt = parse_date(s)
    if not d:
        return s or ""
    if fmt == "%Y":
        return str(d.year)
    if fmt == "%Y-%m":
        return f"{MONTHS[d.month - 1]} {d.year}"
    out = f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
    return (d.strftime("%A") + ", " + out) if weekday else out


def paper_date(p, today):
    """Readable date for a paper. Journals sometimes print future issue dates; say so instead of
    showing a date that looks like a mistake."""
    added = p.get("added_date") or p.get("first_seen", "")[:10]
    pub = p.get("pub_date", "")
    m = re.match(r"(\d{4})", pub)
    if m and int(m.group(1)) > today.year:
        return f"Added to PubMed {nice_date(added)} (journal issue dated {pub})"
    return f"Published {pub}" if pub else f"Added to PubMed {nice_date(added)}"


def age_text(t):
    lo, hi = t.get("min_age_years"), t.get("max_age_years")

    def fmt(v):
        return f"{int(v)}" if float(v).is_integer() else f"{v}"
    if lo is None and hi is None:
        return "Any age"
    if hi is None:
        return f"{fmt(lo)} and older"
    if lo is None:
        return f"Up to {fmt(hi)}"
    return f"{fmt(lo)} to {fmt(hi)}"


def sex_text(s):
    return {"MALE": "Males only", "FEMALE": "Females only"}.get((s or "").upper(), "Any sex")


def next_update(today):
    days = (UPDATE_WEEKDAY - today.weekday()) % 7 or 7
    return today + timedelta(days=days)


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def headline(s):
    if s.get("baseline"):
        c = s["counts"]
        return (f"Tracking {c['active_trials']} active RP trials, {c['recruiting']} of them recruiting "
                f"in {c['countries_recruiting']} countries.")
    parts = []
    recruiting = sum(1 for c in s["status_changes"] if c["to"] == "RECRUITING")
    if recruiting:
        parts.append(_plural(recruiting, "trial", "trials") + " started recruiting")
    if s["new_results"]:
        parts.append(_plural(len(s["new_results"]), "trial", "trials") + " posted results")
    if s["new_trials"]:
        parts.append(_plural(len(s["new_trials"]), "new trial", "new trials") + " registered")
    if s["new_papers"]:
        parts.append(_plural(len(s["new_papers"]), "new paper", "new papers"))
    if not parts:
        other = len(s["status_changes"]) - recruiting
        if other:
            return f"This week: {_plural(other, 'trial changed', 'trials changed')} status."
        return "A quiet week: no trial changes and no new papers."
    return "This week: " + ", ".join(parts) + "."


def read_note():
    path = os.path.join(HERE, "content", "note.md")
    if not os.path.exists(path):
        return None
    text = open(path, encoding="utf-8").read().strip()
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S).strip()
    if not text:
        return None
    title = None
    if text.startswith("# "):
        title, _, text = text.partition("\n")
        title = title[2:].strip()
    return {"title": title, "body": text.strip()}


def make_env(site):
    env = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")),
                      autoescape=select_autoescape(["html", "xml"]))
    env.filters["markdown"] = lambda t: Markup(md.markdown(t or "", extensions=["extra"]))
    env.filters["nice_date"] = nice_date
    env.filters["pipes"] = lambda xs: "|".join(str(x) for x in xs)
    env.filters["age_text"] = age_text
    env.filters["sex_text"] = sex_text
    env.globals.update(site_name=SITE_NAME, status_labels=fetcher.STATUS_LABELS, **site)
    return env


class Site:
    def __init__(self, out, globals_):
        self.out = out
        self.env = make_env(globals_)

    def render(self, rel_path, template, page="", root=None, **ctx):
        if root is None:
            root = "../" * rel_path.count("/")
        html = self.env.get_template(template).render(root=root, u=lambda p: root + p, page=page,
                                                      rel_path=rel_path, **ctx)
        dest = os.path.join(self.out, rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(html)


# ---------------- build ----------------

def build(out):
    store.init_db()
    today = datetime.now(timezone.utc).date()
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    shutil.copytree(os.path.join(HERE, "static"), os.path.join(out, "static"))

    site_cfg = load_json("site.json", {})
    therapy_data = load_json("therapies.json", {"therapies": [], "trial_notes": {}})
    therapies = therapy_data.get("therapies", [])
    trial_notes = therapy_data.get("trial_notes", {})
    news = sorted(load_json("news.json", {"items": []}).get("items", []), key=lambda n: n["date"], reverse=True)
    papers_plain = load_json("papers_plain.json", {"papers": {}}).get("papers", {})
    therapy_by_nct = {n: th for th in therapies for n in th.get("ncts", [])}
    therapy_by_id = {th["id"]: th for th in therapies}
    for n in news:
        n["therapy_obj"] = therapy_by_id.get(n.get("therapy"))

    runs = store.list_runs(limit=500)
    note = read_note()
    trials = [fetcher.derive(t) for t in store.all_trials()]
    trials = [t for t in trials if fetcher.is_relevant_trial(t)]
    for t in trials:
        th = therapy_by_nct.get(t["nct_id"])
        t["therapy"] = th
        t["approved"] = bool(th and th.get("approved"))
        t["note"] = trial_notes.get(t["nct_id"])
    tmap = {t["nct_id"]: t for t in trials}

    papers = [p for p in store.recent_papers(limit=400) if fetcher.is_relevant_paper(p)]
    for p in papers:
        p["plain"] = papers_plain.get(p["pmid"])
        p["kind"] = fetcher.paper_kind(p.get("pubtypes", []), p["title"], p.get("journal", ""))
        p["date_text"] = paper_date(p, today)

    last_run = runs[0]["run_at"] if runs else None
    globals_ = {
        "last_updated": nice_date(last_run[:10], weekday=True) if last_run else None,
        "next_update": nice_date(next_update(today), weekday=True),
        "feedback_url": site_cfg.get("feedback_url", ""),
        "feedback_field": site_cfg.get("feedback_page_field", ""),
    }
    site = Site(out, globals_)

    active = [t for t in trials if t["active"]]
    recruiting = [t for t in trials if t["status"] == "RECRUITING"]
    counts = {
        "active": len(active),
        "recruiting": len(recruiting),
        "recruiting_countries": len({c for t in recruiting for c in t["countries"]}),
        "treatment": sum(1 for t in active if t["is_treatment"]),
        "observational": sum(1 for t in active if not t["is_treatment"]),
        "phase3": sum(1 for t in active if t["is_treatment"] and t["phase_rank"] >= 3),
        "total": len(trials),
    }

    def week_ctx(run):
        s = run["summary"]
        changes = [dict(c, trial=tmap[c["nct_id"]]) for c in s["status_changes"] if c["nct_id"] in tmap]
        changes.sort(key=lambda c: (c["to"] != "RECRUITING", c["to"]))
        stopped = [c for c in changes if c["to"] in ("TERMINATED", "SUSPENDED", "WITHDRAWN")]
        is_latest = run["id"] == runs[0]["id"]
        wanted = set(s["new_papers"])
        wk_papers = sorted([p for p in papers if p["pmid"] in wanted],
                           key=lambda p: (PAPER_KIND_ORDER.get(p["kind"], 9), p.get("sort_date", "")))
        return dict(
            run=run, runs=runs, s=s, headline=headline(s), is_latest=is_latest,
            new_trials=[tmap[i] for i in s["new_trials"] if i in tmap],
            changes=[c for c in changes if c not in stopped], stopped=stopped,
            new_results=[tmap[i] for i in s["new_results"] if i in tmap],
            updated=[tmap[i] for i in s["updated"] if i in tmap],
            papers=wk_papers, note=note if is_latest else None,
            counts=counts, therapies=therapies, news=news[:6],
        )

    if runs:
        site.render("index.html", "week.html", page="home", **week_ctx(runs[0]))
        for r in runs:
            site.render(f"week/{r['id']}.html", "week.html", page="home", **week_ctx(r))
        site.render("weeks.html", "weeks.html", page="weeks",
                    weeks=[dict(r, headline=headline(r["summary"])) for r in runs])
    else:
        site.render("index.html", "empty.html", page="home")

    rows = sorted(trials, key=lambda t: (STATUS_RANK.get(t["status"], 9), not t["is_treatment"],
                                         -t["phase_rank"], t["sponsor"]))
    site.render("trials.html", "trials.html", page="trials", rows=rows, counts=counts,
                approaches=sorted({a for t in trials for a in t["approaches"]}),
                genes=sorted({g for t in trials for g in t["genes"]}),
                countries=sorted({c for t in trials for c in t["countries"]}),
                states=sorted({s for t in trials for s in t["us_states"]}))

    for t in trials:
        by_country = defaultdict(list)
        for loc in t["locations"]:
            by_country[loc["country"] or "Unspecified"].append(loc)
        site.render(f"trial/{t['nct_id']}.html", "trial.html", page="trials", t=t,
                    by_country=dict(sorted(by_country.items(), key=lambda kv: (kv[0] != "United States", kv[0]))))

    # pipeline: active treatment trials by approach > phase > treatment name (each trial counted once)
    pipe = [t for t in active if t["is_treatment"] and t["phase_rank"] >= 0]
    grid = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for t in pipe:
        for label, ranks in PHASE_COLUMNS:
            if t["phase_rank"] in ranks:
                grid[t["approaches"][0]][label][t["lead"]].append(t)
    order = sorted(grid, key=lambda a: -sum(len(ts) for col in grid[a].values() for ts in col.values()))
    site.render("pipeline.html", "pipeline.html", page="pipeline", grid=grid, order=order,
                columns=[c[0] for c in PHASE_COLUMNS], total=len(pipe),
                treatments=len({t["lead"].lower() for t in pipe}),
                country_counts=Counter(c for t in pipe for c in t["countries"]).most_common(15))

    site.render("papers.html", "papers.html", page="papers", rows=papers,
                kinds=sorted({p["kind"] for p in papers}, key=lambda k: PAPER_KIND_ORDER.get(k, 9)))
    site.render("start.html", "start.html", page="start", therapies=therapies)
    site.render("glossary.html", "glossary.html", page="glossary")
    site.render("about.html", "about.html", page="about", conditions=fetcher.CONDITIONS)

    base = os.environ.get("SITE_URL", "").rstrip("/") + "/"
    base = base.replace("http://", "https://")
    site.render("404.html", "message.html", root=base if base != "/" else "", title="Page not found",
                body="That page doesn't exist. It may have been a trial that is no longer in scope.")

    items = [{"title": f"Week of {nice_date(r['id'][:10])}: {headline(r['summary'])}",
              "link": f"{base}week/{r['id']}.html",
              "date": format_datetime(datetime.fromisoformat(r["run_at"])),
              "desc": r["summary"].get("ai_summary") or headline(r["summary"])} for r in runs[:52]]
    site.render("feed.xml", "feed.xml", items=items, base=base)

    cname = os.path.join(HERE, "CNAME")
    if os.path.exists(cname):
        shutil.copy(cname, os.path.join(out, "CNAME"))
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"Built {out}: {len(trials)} trials ({counts['active']} active), {len(papers)} papers, {len(runs)} weeks")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="pull fresh data before building")
    ap.add_argument("--out", default=os.path.join(HERE, "site"))
    args = ap.parse_args()
    if args.fetch:
        refresh.run()
    build(args.out)
