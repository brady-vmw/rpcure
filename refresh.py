"""Weekly refresh job.

Run it with:  python refresh.py
Normally run by build.py --fetch from the weekly GitHub Action.
"""
import json
import os
import sys

import requests

import fetcher
import store

ACTIVE_ORDER = ["RECRUITING", "NOT_YET_RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING"]


def ai_summary(summary, trials_by_id, papers_by_id):
    """Optional plain-language digest via Claude. Needs ANTHROPIC_API_KEY."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return ""

    def tline(nct):
        t = trials_by_id.get(nct)
        if not t:
            return nct
        return (f"{nct}: {t['title']} | sponsor {t['sponsor']} | {t['phase_label']} | "
                f"{t['status_label']} | approach {', '.join(t['approaches'])} | "
                f"genes {', '.join(t['genes']) or 'not specified'}")

    lines = ["NEW TRIALS:"] + [tline(n) for n in summary["new_trials"]]
    lines += ["STATUS CHANGES:"] + [
        f"{tline(c['nct_id'])} (was {fetcher.STATUS_LABELS.get(c['from'], c['from'])})"
        for c in summary["status_changes"]]
    lines += ["RESULTS NEWLY POSTED:"] + [tline(n) for n in summary["new_results"]]
    lines += ["NEW PAPERS:"] + [
        f"{p['title']} ({p['journal']}; {p['kind']})"
        for p in (papers_by_id.get(i) for i in summary["new_papers"]) if p]

    prompt = (
        "You write the weekly update for a website read by people with retinitis pigmentosa (RP) "
        "and their families. Below is this week's raw change log from ClinicalTrials.gov and "
        "PubMed. Write 120-220 words of plain English: lead with anything a patient could act on "
        "(trials that started recruiting, results posted, late-stage trials changing status), then "
        "notable science. Name trials by sponsor and drug. Do not hype lab or animal work as a "
        "cure; say plainly when something is early-stage. If the week is quiet, say so in one or "
        "two sentences. No medical advice, no headings, no bullet lists.\n\n" + "\n".join(lines)
    )
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
                  "max_tokens": 600,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=90,
        )
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", [])).strip()
    except Exception as e:  # never let the digest break the refresh
        print(f"AI summary skipped: {e}", file=sys.stderr)
        return ""


def run(trials=None, papers=None):
    store.init_db()
    run_at = store.now_iso()
    run_id, n = run_at[:10], 2
    while store.get_run(run_id):  # more than one run on the same day
        run_id, n = f"{run_at[:10]}-{n}", n + 1

    first_run = not store.list_runs(limit=1)

    if trials is None:
        trials = fetcher.fetch_trials()
    if papers is None:
        papers = fetcher.fetch_papers(days=30 if first_run else 8)

    new_trials, status_changes, new_results, updated = [], [], [], []
    for t in trials:
        if not t.get("nct_id"):
            continue
        before = store.get_trial(t["nct_id"])
        changes = store.upsert_trial(t, run_at)
        if "new" in changes:
            new_trials.append(t["nct_id"])
        if "status" in changes:
            status_changes.append({"nct_id": t["nct_id"], "from": before["status"], "to": t["status"]})
        if "results" in changes:
            new_results.append(t["nct_id"])
        if "updated" in changes and not ({"status", "results"} & changes):
            updated.append(t["nct_id"])

    new_papers = [p["pmid"] for p in papers if store.upsert_paper(p, run_at)]

    trials_by_id = {t["nct_id"]: t for t in trials}
    active = [t for t in trials if t["active"]]
    summary = {
        "baseline": first_run,
        "new_trials": [] if first_run else new_trials,
        "status_changes": status_changes,
        "new_results": new_results,
        "updated": updated,
        "new_papers": new_papers,
        "counts": {
            "total_trials": len(trials),
            "active_trials": len(active),
            "recruiting": sum(1 for t in trials if t["status"] == "RECRUITING"),
            "phase3_active": sum(1 for t in active if t["phase_rank"] >= 3),
            "countries_recruiting": len({c for t in trials if t["status"] == "RECRUITING"
                                         for c in t["countries"]}),
        },
        "ai_summary": "",
    }
    if not first_run:
        papers_by_id = {p["pmid"]: p for p in papers}
        summary["ai_summary"] = ai_summary(summary, trials_by_id, papers_by_id)

    store.save_run(run_id, run_at, summary)
    store.save()
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in summary.items()
                      if k != "ai_summary"}, indent=2))
    return run_id


if __name__ == "__main__":
    run()
