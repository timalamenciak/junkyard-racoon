# power-tools

The nightly research intelligence pipeline. Orchestrated by `nightly_run.py`.

For setup, installation, and deployment instructions see the [root README](../README.md).

---

## Pipeline steps

```
nightly_run.py
│
├── ingest/collaborator_publications.py   ORCID API → collaborator_publications.json
├── ingest/gmail_imap_bridge.py           Gmail IMAP → email_messages.json  [allow_failure]
├── ingest/rss_journals.py                RSS + email merge → journal_articles.json
├── ingest/grant_opportunities.py         RSS + email merge → grant_opportunities.json
├── ingest/research_news.py               RSS + email merge + keyword filter → news_items.json
├── ingest/job_openings.py                email + web scrapers → job_openings.json
│
├── processing/score_articles.py          LLM relevance score → scored_articles.json
├── processing/score_grants.py            LLM relevance score → scored_grants.json
├── processing/score_jobs.py              LLM student-fit score → scored_jobs.json
├── processing/obsidian_todos.py          LLM per-note task extraction → obsidian_todos.json
├── processing/daily_digest.py            Assembles digest + Mastodon toots → daily_digest.json/.md
│
├── output/publish_hedgedoc.py            Pushes to HedgeDoc wiki  [allow_failure]
├── output/publish_static_digest.py       Builds filterable HTML site
├── output/matrix_digest.py               Renders matrix_digest.txt
├── output/post_matrix_digest.py          Posts digest to Matrix room  [allow_failure]
└── output/podcast_script.py             LLM podcast script → podcast_script.md
```

---

## Scoring thresholds

Configured in `configs/lab_profile.yaml`:

| Pipeline | Key | Default |
|----------|-----|---------|
| Articles | `article_relevance_threshold` | 0.75 |
| Grants | `grant_relevance_threshold` | 0.65 |
| Jobs | all surfaced, sorted by student fit score | — |
| News | keyword score ≥ 1 (set in `news.yaml`) | — |

---

## Tasks pipeline

`obsidian_todos.py` runs a two-stage LLM flow:

1. Each project note is sent individually: *"Identify the next 3-5 tasks for this project"*
2. All collected tasks are sent together: *"Which of these are high impact?"*

Vault paths and glob patterns for project files are set in `lab_profile.yaml` under `obsidian_vault_paths` and `todo_project_globs`.

---

## Collaborator publications

`collaborator_publications.py` queries the ORCID public API for each collaborator listed in `configs/collaborators.yaml`. Only works published within `days_back` days (default: 14) are included.

---

## Static digest site

`publish_static_digest.py` maintains rolling state in `data/state/static_digest_site.json`:

- Digest history: 60 days
- Jobs board: 120 days, deduplicated by title + org + location + link; expired deadlines are pruned each run

The generated site has a filterable, sortable jobs table and daily digest sections. Deploy path and public URL are set in `configs/output.yaml` under `static_site`.

---

## Configs

| File | Purpose |
|------|---------|
| `llm.yaml` | LLM endpoint and credentials (shared with Matrix bot) |
| `lab_profile.yaml` | Lab identity, research interests, scoring thresholds, Obsidian vault |
| `output.yaml` | Static site, HedgeDoc, Matrix, podcast output paths |
| `email_ingest.yaml` | Gmail IMAP connection and label-to-pipeline routing |
| `journals.yaml` | Journal RSS feeds with topic tags |
| `news.yaml` | News RSS feeds with keyword scoring |
| `grants.yaml` | Grant RSS feeds |
| `jobs.yaml` | Job board URLs to scrape |
| `collaborators.yaml` | Collaborator names and ORCID IDs |

---

## Test mode

Every script honours `--test` / `POWER_TOOLS_TEST_MODE=1`. In test mode all scripts generate sample output without calling external APIs.

```bash
python nightly_run.py --test
```

---

## Extending the pipeline

- **New ingest source:** `docs/adding_ingest_sources.md`
- **New email parser:** `docs/adding_email_parsers.md`
