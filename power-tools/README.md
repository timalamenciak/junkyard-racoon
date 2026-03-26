# Power Tools

This folder separates long-running and scheduled automation from the interactive Matrix bot.

## Layers

### 1. Ingest

Pulls raw records from external systems into local JSON snapshots.

- `ingest/rss_journals.py`
- `ingest/grant_opportunities.py`
- `ingest/research_news.py`
- `ingest/job_openings.py`
- `ingest/collaborator_publications.py`
- `ingest/gmail_imap_bridge.py`

### 2. Processing

Turns raw records into scored, summarized, and prioritized artifacts.

- `processing/score_articles.py`
- `processing/score_grants.py`
- `processing/obsidian_todos.py`
- `processing/daily_digest.py`

The Obsidian task flow now:

- reads each project file
- converts each project file into a list of concrete tasks
- asks the LLM to prioritize the combined task list by high impact and low effort first

### 3. Output

Publishes or formats processed artifacts for downstream systems.

- `output/publish_hedgedoc.py`
- `output/publish_static_digest.py`
- `output/serve_static_digest.py`
- `output/matrix_digest.py`
- `output/podcast_script.py`

The HedgeDoc publisher now keeps rolling two-month article, grant, task, and jobs notes.
Each run prepends a new dated section to the top of the current rollup, and when the two-month window changes it publishes the old rollup to an archive note before starting a fresh one.
The jobs note renders a live open-positions board with two tables, one for broad conservation jobs and one for academic biodiversity restoration/conservation roles.
The static digest publisher builds a lab-facing HTML site with a rolling digest history and one continuously updated jobs table.

## Configs

Configs live under `configs/`:

- `journals.yaml`
- `grants.yaml`
- `collaborators.yaml`
- `news.yaml`
- `lab_profile.yaml`
- `llm.yaml`
- `output.yaml`
- `email_ingest.yaml`

The email ingest config follows the same copy-from-example pattern as other local configs:

```yaml
email_ingest:
  enabled: true
  provider: gmail_imap
  host: imap.gmail.com
  username_env: JUNKYARD_GMAIL_USERNAME
  password_env: JUNKYARD_GMAIL_APP_PASSWORD
  labels:
    - pivot
    - grants
    - journals
    - news
    - jobs
  lookback_days: 14
  max_messages_per_label: 50
  unread_only: false

routing:
  email_label_map:
    pivot: grant_opportunities
    grants: grant_opportunities
    journals: journal_articles
    news: news_items
    jobs: job_openings
```

## Data

Generated files are written under `data/`:

- `data/ingest`
- `data/processing`
- `data/output`

## Typical Nightly Flow

```powershell
py -3 power-tools\nightly_run.py
```

For a non-destructive preview run that generates sample artifacts instead of calling external systems:

```powershell
py -3 power-tools\nightly_run.py --test
```

Sample scheduler definitions live in [`deploy/power-tools-nightly.cron`](/C:/Users/Tim Alamenciak/Documents/RacoonLab/junkyard-racoon/deploy/power-tools-nightly.cron), [`deploy/junkyard-racoon-nightly.service`](/C:/Users/Tim Alamenciak/Documents/RacoonLab/junkyard-racoon/deploy/junkyard-racoon-nightly.service), and [`deploy/junkyard-racoon-nightly.timer`](/C:/Users/Tim Alamenciak/Documents/RacoonLab/junkyard-racoon/deploy/junkyard-racoon-nightly.timer).
For reverse-proxying the generated digest from another VM, use [`deploy/junkyard-racoon-digest.service`](/C:/Users/Tim Alamenciak/Documents/RacoonLab/junkyard-racoon/deploy/junkyard-racoon-digest.service), which serves `power-tools/data/output/static_digest_site` on `127.0.0.1:8085` by default.

Journal RSS ingest now records seen article keys in `power-tools/data/state/rss_seen_articles.json` so the same article is not surfaced repeatedly on subsequent real runs.
The Gmail IMAP bridge writes routed raw email records to `power-tools/data/ingest/email_messages.json`, and journal/grant ingesters merge those into their existing JSON snapshots.
The `research_news.py` ingester normalizes email-routed `news_items` messages into `power-tools/data/ingest/news_items.json`.
The `job_openings.py` ingester normalizes email-routed `job_openings` messages into `power-tools/data/ingest/job_openings.json`.
It also ingests RSS-based research news from `configs/news.yaml` and applies a transparent keyword filter before writing the combined snapshot.
Email credentials are read from environment variables named in `configs/email_ingest.yaml`, for example `JUNKYARD_GMAIL_USERNAME` and `JUNKYARD_GMAIL_APP_PASSWORD`.
Routing is label-driven: labels listed under `email_ingest.labels` are matched to downstream targets via `routing.email_label_map`.
Gmail labels are the primary routing signal. Optional `from_contains` and `subject_contains` rules act only as backup filters within a selected labeled mailbox.

## Recommended Matrix Bot Role

The Matrix bot should read and relay artifacts from `power-tools/data/output/` by default.

That keeps scheduling in cron or systemd, ingest and processing in `power-tools`, and chat interaction in the Matrix daemon.

If you want manual intervention later, the bot can trigger selected output-only scripts, but it should not own the nightly ingest and processing pipeline.
