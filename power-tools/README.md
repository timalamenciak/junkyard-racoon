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

The Obsidian task flow now:

- reads each project file
- converts each project file into a list of concrete tasks
- asks the LLM to prioritize the combined task list by high impact and low effort first

### 3. Output

Publishes or formats processed artifacts for downstream systems.

- `output/publish_hedgedoc.py`
- `output/matrix_digest.py`
- `output/podcast_script.py`

The HedgeDoc publisher now keeps rolling two-month article, grant, and task lists.
Each run prepends a new dated section to the top of the current rollup, and when the two-month window changes it publishes the old rollup to an archive note before starting a fresh one.

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

For a non-destructive preview run that generates sample artifacts instead of calling external systems:

```powershell
py -3 power-tools\nightly_run.py --test
```

Sample scheduler definitions live in [`deploy/power-tools-nightly.cron`](/C:/Users/Tim Alamenciak/Documents/RacoonLab/junkyard-racoon/deploy/power-tools-nightly.cron), [`deploy/junkyard-racoon-nightly.service`](/C:/Users/Tim Alamenciak/Documents/RacoonLab/junkyard-racoon/deploy/junkyard-racoon-nightly.service), and [`deploy/junkyard-racoon-nightly.timer`](/C:/Users/Tim Alamenciak/Documents/RacoonLab/junkyard-racoon/deploy/junkyard-racoon-nightly.timer).

Journal RSS ingest now records seen article keys in `power-tools/data/state/rss_seen_articles.json` so the same article is not surfaced repeatedly on subsequent real runs.

## Recommended Matrix Bot Role

The Matrix bot should read and relay artifacts from `power-tools/data/output/` by default.

That keeps scheduling in cron or systemd, ingest and processing in `power-tools`, and chat interaction in the Matrix daemon.

If you want manual intervention later, the bot can trigger selected output-only scripts, but it should not own the nightly ingest and processing pipeline.
