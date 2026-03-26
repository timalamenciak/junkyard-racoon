# Email Ingest Upgrade Plan

## 1. Current Architecture Summary

`junkyard-racoon` already follows a simple staged pipeline under `power-tools/`:

1. Ingest scripts pull external data into JSON snapshots under `power-tools/data/ingest/`.
2. Processing scripts read those snapshots, score or prioritize them, and write outputs under `power-tools/data/processing/`.
3. Output scripts assemble and publish final artifacts from processed JSON under `power-tools/data/output/`.
4. `power-tools/nightly_run.py` is the main orchestration entrypoint for the nightly workflow.

The current design is file-oriented rather than framework-oriented. Each stage communicates primarily through well-named JSON files.

## 2. Existing Relevant Modules/Files

### Ingestion scripts

- `power-tools/ingest/rss_journals.py`
- `power-tools/ingest/grant_opportunities.py`
- `power-tools/ingest/collaborator_publications.py`

### Nightly orchestration entrypoint(s)

- `power-tools/nightly_run.py`
- Scheduler wrappers:
  - `deploy/power-tools-nightly.cron`
  - `deploy/junkyard-racoon-nightly.service`
  - `deploy/junkyard-racoon-nightly.timer`

### Current JSON outputs and schema shape

`power-tools/data/ingest/journal_articles.json`

```json
{
  "generated_at": "ISO-8601 timestamp",
  "items": [
    {
      "source_type": "journal_rss",
      "feed": "feed name",
      "title": "string",
      "link": "url",
      "summary": "string",
      "published": "ISO-8601 timestamp or 'unknown'",
      "tags": ["..."],
      "article_key": "dedupe key"
    }
  ]
}
```

`power-tools/data/ingest/grant_opportunities.json`

```json
{
  "generated_at": "ISO-8601 timestamp",
  "items": [
    {
      "source_type": "grant_rss",
      "source": "source name",
      "title": "string",
      "link": "url",
      "summary": "string",
      "published": "ISO-8601 timestamp or 'unknown'",
      "tags": ["..."]
    }
  ]
}
```

`power-tools/data/ingest/collaborator_publications.json`

```json
{
  "generated_at": "ISO-8601 timestamp",
  "items": [
    {
      "collaborator": "string",
      "title": "string",
      "link": "url",
      "published": "YYYY-MM-DD",
      "source": "orcid"
    }
  ]
}
```

`power-tools/data/processing/scored_articles.json`

```json
{
  "generated_at": "ISO-8601 timestamp",
  "items": [{ "...ingest fields...", "relevance_score": 0.0, "llm_summary": "string", "rationale": "string", "recommended_action": "string" }],
  "relevant_items": [{ "...same shape as items..." }],
  "test_mode": true
}
```

`power-tools/data/processing/scored_grants.json`

```json
{
  "generated_at": "ISO-8601 timestamp",
  "items": [{ "...ingest fields...", "relevance_score": 0.0, "llm_summary": "string", "rationale": "string", "next_step": "string" }],
  "relevant_items": [{ "...same shape as items..." }],
  "test_mode": true
}
```

### Existing parser abstractions

There is no shared ingestion parser interface yet. The current pattern is lightweight script-local parsing plus a few shared helpers:

- `power-tools/common/io_utils.py` for config/data path helpers and JSON/YAML read-write
- `power-tools/common/http_utils.py` for HTTP fetches
- `power-tools/common/runtime.py` for test-mode detection
- Script-local functions such as `strip_html()`, `parse_date()`, and record constructors in each ingester

### RSS configuration locations

- `power-tools/configs/journals.yaml`
- `power-tools/configs/grants.yaml`

### Scoring locations

- `power-tools/processing/score_articles.py`
- `power-tools/processing/score_grants.py`
- Shared LLM helper usage in `power-tools/common/llm.py`

## 3. Proposed Insertion Point for IMAP Ingestion

The lowest-disruption insertion point is a new ingest-stage script that runs before existing RSS/grant scoring steps and produces an intermediate email snapshot, for example:

- `power-tools/ingest/gmail_imap_bridge.py`

This script should run in the nightly sequence before:

- `power-tools/ingest/rss_journals.py`
- `power-tools/ingest/grant_opportunities.py`

That keeps the current downstream processing unchanged and allows existing ingesters to merge email-derived records into the same JSON outputs they already own.

## 4. Proposed Data Flow

```text
Gmail IMAP
-> label/mailbox routing config
-> fetch matching messages
-> conservative parse of subject/date/body/links/from
-> normalized intermediate email snapshot
-> map routed email messages into:
   - journal_articles.json
   - grant_opportunities.json
-> existing scoring scripts
-> existing digest/output pipeline
```

Recommended concrete flow:

1. Add `power-tools/configs/email_ingest.yaml` for IMAP host, env-var names, label routes, and route targets.
2. Add a new email ingest script that writes `power-tools/data/ingest/email_messages.json`.
3. Extend `rss_journals.py` to merge route target `journals` into `journal_articles.json`.
4. Extend `grant_opportunities.py` to merge route target `grants` into `grant_opportunities.json`.
5. Leave `score_articles.py`, `score_grants.py`, and the output layer unchanged unless a field mismatch forces a small compatibility adjustment.

## 5. Schema Mismatches That Need To Be Handled

The main mismatch is that email messages are not article or grant records by default.

Issues to handle conservatively:

- Email has `subject`, `from`, mailbox/label metadata, and body text; current downstream files expect `title`, `summary`, and either `feed` or `source`.
- Journal ingest uses `article_key` and a seen-state file for deduplication; email-derived journal items need a stable equivalent key.
- Some emails may not contain a canonical link. Downstream code tolerates empty strings better than invented URLs.
- Email bodies are noisier than RSS summaries. Body extraction should be conservative and truncated.
- Gmail labels are routing metadata, not necessarily user-facing feed/source names. A small mapping layer is needed.

Minimal compatibility mapping:

- email `subject` -> normalized `title`
- email body snippet -> normalized `summary`
- email link if present -> normalized `link`
- email label/route name -> `feed` for journal-like items or `source` for grant-like items
- email message id / label / date -> dedupe key for article-like items
- preserve extra debug metadata such as `gmail_label`, `message_id`, and `email_from` as additive fields

## 6. Minimal Implementation Plan

1. Add `power-tools/configs/email_ingest.yaml.example` with route definitions and env-var credential names.
2. Add a shared helper module for IMAP connection, message decoding, HTML stripping, and conservative link extraction.
3. Add `power-tools/ingest/gmail_imap_bridge.py` that writes a raw normalized email snapshot to `power-tools/data/ingest/email_messages.json`.
4. Extend `rss_journals.py` to optionally merge journal-routed email items into `journal_articles.json`.
5. Extend `grant_opportunities.py` to optionally merge grant-routed email items into `grant_opportunities.json`.
6. Insert the new email ingest script into `power-tools/nightly_run.py` before the current journal/grant ingest steps.
7. Reuse existing processing and output stages without broad refactoring.
8. Add `--test` sample email items so the nightly pipeline can still be exercised non-destructively.

This approach preserves the current architecture: ingest scripts still own ingest snapshots, processing scripts still read the same file names, and email becomes an additional first-class source rather than a separate parallel pipeline.
