"""JSON-file storage, committed to the repo so each weekly run can diff against the last.

All state lives in data/state.json:
  trials: {nct_id: {...normalized trial..., first_seen, prev_status, status_changed_at, ...}}
  papers: {pmid: {...normalized paper..., first_seen}}
  runs:   [ {id, run_at, summary}, ... ]  newest first
"""
import json
import os
from datetime import datetime, timezone

STATE_PATH = os.environ.get("STATE_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "state.json"))
_state = None


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def init_db():
    global _state
    if _state is not None:
        return
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH, encoding="utf-8") as f:
            _state = json.load(f)
    else:
        _state = {"trials": {}, "papers": {}, "runs": []}


def save():
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_state, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def reset():
    """Forget in-memory state (tests)."""
    global _state
    _state = None


# ---------- trials ----------

def get_trial(nct_id):
    init_db()
    return _state["trials"].get(nct_id)


def upsert_trial(t, run_at):
    """Insert or update a normalized trial. Returns change tags: new, status, results, updated."""
    init_db()
    old = _state["trials"].get(t["nct_id"])
    if old is None:
        _state["trials"][t["nct_id"]] = dict(t, first_seen=run_at, prev_status=None,
                                              status_changed_at=None, results_seen_at=None)
        return {"new"}
    changes = set()
    rec = dict(t, first_seen=old["first_seen"], prev_status=old.get("prev_status"),
               status_changed_at=old.get("status_changed_at"), results_seen_at=old.get("results_seen_at"))
    if old["status"] != t["status"]:
        changes.add("status")
        rec["prev_status"], rec["status_changed_at"] = old["status"], run_at
    if t["has_results"] and not old["has_results"]:
        changes.add("results")
        rec["results_seen_at"] = run_at
    if (t["last_update"] or "") > (old.get("last_update") or ""):
        changes.add("updated")
    _state["trials"][t["nct_id"]] = rec
    return changes


def all_trials():
    init_db()
    return list(_state["trials"].values())


def trials_by_ids(ids):
    init_db()
    return [_state["trials"][i] for i in ids if i in _state["trials"]]


# ---------- papers ----------

def upsert_paper(p, run_at):
    init_db()
    old = _state["papers"].get(p["pmid"])
    _state["papers"][p["pmid"]] = dict(p, first_seen=old["first_seen"] if old else run_at)
    return old is None


def recent_papers(limit=300):
    init_db()
    rows = sorted(_state["papers"].values(),
                  key=lambda p: (p["first_seen"], p.get("sort_date", "")), reverse=True)
    return rows[:limit]


def papers_by_ids(ids):
    init_db()
    return [_state["papers"][i] for i in ids if i in _state["papers"]]


# ---------- runs ----------

def save_run(run_id, run_at, summary):
    init_db()
    _state["runs"].insert(0, {"id": run_id, "run_at": run_at, "summary": summary})


def list_runs(limit=60):
    init_db()
    return _state["runs"][:limit]


def get_run(run_id):
    init_db()
    return next((r for r in _state["runs"] if r["id"] == run_id), None)
