"""Build the static site.

  python build.py           render site/ from data/state.json (no network)
  python build.py --fetch   pull fresh data first (the weekly GitHub Action does this)

Env: SITE_URL (absolute base for the RSS feed), SITE_NAME, ANTHROPIC_API_KEY, NCBI_EMAIL, NCBI_API_KEY.
"""
import argparse
import os
import shutil
from collections import Counter, defaultdict
from email.utils import format_datetime
from datetime import datetime

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
PAPER_KIND_ORDER = {"Human trial": 0, "Research": 1, "Review": 2, "Lab / animal": 3, "Case report": 4}


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
    lines = [l for l in text.splitlines() if not l.strip().startswith("<!--")]
    text = "\n".join(lines).strip()
    if not text:
        return None
    title = None
    if text.startswith("# "):
        title, _, text = text.partition("\n")
        title = title[2:].strip()
    return {"title": title, "body": text.strip()}


def make_env():
    env = Environment(loader=FileSystemLoader(os.path.join(HERE, "templates")),
                      autoescape=select_autoescape(["html", "xml"]), trim_blocks=False)
    env.filters["markdown"] = lambda t: Markup(md.markdown(t or "", extensions=["extra"]))
    env.filters["short_date"] = lambda s: (s or "")[:10]
    env.filters["pipes"] = lambda xs: "|".join(xs)
    env.globals.update(site_name=SITE_NAME, status_labels=fetcher.STATUS_LABELS)
    return env


class Site:
    def __init__(self, out):
        self.out = out
        self.env = make_env()

    def render(self, rel_path, template, page="", root=None, **ctx):
        if root is None:
            root = "../" * rel_path.count("/")
        html = self.env.get_template(template).render(root=root, u=lambda p: root + p, page=page, **ctx)
        dest = os.path.join(self.out, rel_path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(html)


def week_context(run, runs, note):
    s = run["summary"]
    ids = set(s["new_trials"]) | {c["nct_id"] for c in s["status_changes"]} | set(s["new_results"]) | set(s["updated"])
    tmap = {t["nct_id"]: t for t in store.trials_by_ids(ids)}
    papers = sorted(store.papers_by_ids(s["new_papers"]),
                    key=lambda p: (PAPER_KIND_ORDER.get(p["kind"], 9), p.get("sort_date", "")))
    changes = [dict(c, trial=tmap[c["nct_id"]]) for c in s["status_changes"] if c["nct_id"] in tmap]
    changes.sort(key=lambda c: (c["to"] != "RECRUITING", c["to"]))
    is_latest = run["id"] == runs[0]["id"]
    return dict(
        run=run, runs=runs, s=s, headline=headline(s), is_latest=is_latest,
        new_trials=[tmap[i] for i in s["new_trials"] if i in tmap],
        changes=changes,
        new_results=[tmap[i] for i in s["new_results"] if i in tmap],
        updated=[tmap[i] for i in s["updated"] if i in tmap],
        papers=papers,
        note=note if is_latest else None,
    )


def build(out):
    store.init_db()
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    shutil.copytree(os.path.join(HERE, "static"), os.path.join(out, "static"))
    site = Site(out)

    runs = store.list_runs(limit=500)
    note = read_note()
    trials = store.all_trials()

    # home + weekly archive
    if runs:
        site.render("index.html", "week.html", page="home", **week_context(runs[0], runs, note))
        for r in runs:
            site.render(f"week/{r['id']}.html", "week.html", page="home", **week_context(r, runs, note))
    else:
        site.render("index.html", "empty.html", page="home")

    # trials list (filtered in the browser)
    rows = sorted(trials, key=lambda t: (STATUS_RANK.get(t["status"], 9), -t["phase_rank"], t["sponsor"]))
    site.render("trials.html", "trials.html", page="trials", rows=rows,
                approaches=sorted({a for t in trials for a in t["approaches"]}),
                genes=sorted({g for t in trials for g in t["genes"]}),
                countries=sorted({c for t in trials for c in t["countries"]}))

    # one page per trial
    for t in trials:
        by_country = defaultdict(list)
        for loc in t["locations"]:
            by_country[loc["country"] or "Unspecified"].append(loc)
        site.render(f"trial/{t['nct_id']}.html", "trial.html", page="trials", t=t,
                    by_country=dict(sorted(by_country.items())))

    # pipeline
    active = [t for t in trials if t["active"] and t["study_type"] == "INTERVENTIONAL" and t["phase_rank"] >= 0]
    grid = defaultdict(lambda: defaultdict(list))
    for t in active:
        for label, ranks in PHASE_COLUMNS:
            if t["phase_rank"] in ranks:
                grid[t["approaches"][0]][label].append(t)
    order = sorted(grid, key=lambda a: -sum(len(v) for v in grid[a].values()))
    site.render("pipeline.html", "pipeline.html", page="pipeline", grid=grid, order=order,
                columns=[c[0] for c in PHASE_COLUMNS], total=len(active),
                country_counts=Counter(c for t in active for c in t["countries"]).most_common(15))

    papers = store.recent_papers(limit=300)
    site.render("papers.html", "papers.html", page="papers", rows=papers, kinds=sorted({p["kind"] for p in papers}))
    site.render("about.html", "about.html", page="about", conditions=fetcher.CONDITIONS)
    base = os.environ.get("SITE_URL", "").rstrip("/") + "/"
    # 404 can be served at any depth, so it needs absolute links when we know the site URL
    site.render("404.html", "message.html", root=base if base != "/" else "", title="Not found",
                body="That page doesn't exist.")

    # RSS feed of weekly updates
    items = [{"title": f"Week of {r['id'][:10]}: {headline(r['summary'])}",
              "link": f"{base}week/{r['id']}.html",
              "date": format_datetime(datetime.fromisoformat(r["run_at"])),
              "desc": r["summary"].get("ai_summary") or headline(r["summary"])} for r in runs[:52]]
    site.render("feed.xml", "feed.xml", items=items, base=base)

    cname = os.path.join(HERE, "CNAME")
    if os.path.exists(cname):
        shutil.copy(cname, os.path.join(out, "CNAME"))
    open(os.path.join(out, ".nojekyll"), "w").close()
    print(f"Built {out}: {len(trials)} trials, {len(papers)} papers, {len(runs)} weeks")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="pull fresh data before building")
    ap.add_argument("--out", default=os.path.join(HERE, "site"))
    args = ap.parse_args()
    if args.fetch:
        refresh.run()
    build(args.out)
