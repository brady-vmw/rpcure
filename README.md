# RP Cure Tracker

A weekly, plain-language tracker of retinitis pigmentosa research for patients and families,
hosted free on GitHub Pages.

Every Monday a GitHub Action pulls every RP-related trial from ClinicalTrials.gov and new papers
from PubMed, compares them with last week's snapshot (`data/state.json`), commits the new snapshot,
and rebuilds the site. The home page leads with what changed: trials that started recruiting,
results posted, new trials, new papers.

## Pages

| Page | What it shows |
|---|---|
| `index.html` This week | One-sentence headline of the week's changes, optional AI plain-English summary, your note, status changes, results, new trials, new papers, past weeks |
| `trials.html` | Every trial, filterable by status, **gene** (RPGR, USH2A, RHO... or "not gene-specific"), approach, phase, country, search. Filters are in the URL, so `trials.html?gene=RPGR` is shareable |
| `trial/NCT....html` | One trial: sponsor, treatment, ages, sites by country, link to the official record |
| `pipeline.html` | Active treatment trials grouped by approach and phase, plus where sites are |
| `papers.html` | New PubMed papers labeled Human trial / Research / Review / Lab-animal |
| `feed.xml` | RSS of weekly updates (people can get it by email through any RSS-to-email service) |

Built for low vision: one central column, Atkinson Hyperlegible font, navy/amber contrast,
text-size buttons, dark mode.

## Your weekly routine

Nothing is required: the data updates itself every Monday morning.

To add a note to the top of the home page, edit `content/note.md` on github.com (pencil icon),
write a few lines, and commit. The site rebuilds in about a minute. Start with `# Title` for a
heading; leave the file empty to hide the note.

To refresh right now: **Actions → Weekly update → Run workflow**.

## Settings (repo → Settings)

- **Pages:** Source = GitHub Actions. Custom domain goes here too.
- **Secrets and variables → Actions:**
  - Variable `NCBI_EMAIL`: an email address (NCBI asks API users to identify themselves).
  - Secret `ANTHROPIC_API_KEY` (optional): turns on the "In plain English" weekly summary.
  - Secret `NCBI_API_KEY`, variable `SITE_NAME` (optional).

## Run locally

```
pip install -r requirements.txt
python build.py --fetch      # pull data and build into site/
python -m http.server -d site
python tests/test_app.py     # full pipeline on mock data, no network
```

## Limits

- Covers ClinicalTrials.gov, where nearly all company-run RP trials register, including trials
  run only outside the US. Academic trials registered only in ChiCTR (China), EU CTIS, ISRCTN or
  ANZCTR are missed. WHO's ICTRP aggregates these but has no free API.
- Approach and gene labels come from keyword rules in `fetcher.py`; edit `APPROACH_RULES`,
  `GENES` or `CONDITIONS` to tune them.
