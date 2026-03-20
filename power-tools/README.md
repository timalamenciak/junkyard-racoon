# Power Tools

This folder separates long-running and scheduled automation from the interactive Matrix bot.

## Layers

### 1. Ingest

Pulls raw records from external systems into local JSON snapshots.

- `ingest/rss_journals.py`
- `ingest/grant_opportunities.py`
- `ingest/collaborator_publications.py`

### 2. Processing

Turns raw records into scored, summarized, and prioritized artifacts.

- `processing/score_articles.py`
- `processing/score_grants.py`
- `processing/obsidian_todos.py`
- `processing/daily_digest.py`

### 3. Output

Publishes or formats processed artifacts for downstream systems.

- `output/publish_bookstack.py`
- `output/matrix_digest.py`
- `output/podcast_script.py`

## Configs

Configs live under `configs/`:

- `journals.yaml`
- `grants.yaml`
- `collaborators.yaml`
- `lab_profile.yaml`
- `llm.yaml`
- `output.yaml`

## Data

Generated files are written under `data/`:

- `data/ingest`
- `data/processing`
- `data/output`

## Typical Nightly Flow

```powershell
py -3 power-tools\nightly_run.py
```

## Recommended Matrix Bot Role

The Matrix bot should read and relay artifacts from `power-tools/data/output/` by default.

That keeps scheduling in cron or systemd, ingest and processing in `power-tools`, and chat interaction in the Matrix daemon.

If you want manual intervention later, the bot can trigger selected output-only scripts, but it should not own the nightly ingest and processing pipeline.
