# Email Ingest Output Mapping

## Output File Mapping

Email-derived content is mapped into existing ingest outputs wherever possible:

- Pivot/grant alert emails with route target `grant_opportunities` write into `power-tools/data/ingest/grant_opportunities.json`.
- Journal newsletter emails with route target `journal_articles` write into `power-tools/data/ingest/journal_articles.json`.
- Research news emails with route target `news_items` write into `power-tools/data/ingest/news_items.json`.

The first two land in the same files already consumed by downstream scorers. The research-news path uses a minimal new file because there was no pre-existing `news_items` ingest snapshot in the repo.

## Adapter Layer

The IMAP fetch step is separated from normalization:

- `power-tools/ingest/gmail_imap_bridge.py` fetches raw email records and writes `power-tools/data/ingest/email_messages.json`.
- Existing-style ingest adapters then map those raw email records into downstream-compatible snapshots:
  - `power-tools/ingest/grant_opportunities.py`
  - `power-tools/ingest/rss_journals.py`
  - `power-tools/ingest/research_news.py`

This preserves the current RSS parsers rather than replacing or rewriting them.

## Preserved Output Contracts

### Grant opportunities

Email-derived grant items preserve the existing grant snapshot shape:

- `source_type`
- `source`
- `title`
- `link`
- `summary`
- `published`
- `tags`

They are appended alongside RSS-derived grant items in `grant_opportunities.json`.

### Journal articles

Email-derived journal items preserve the existing journal snapshot shape:

- `source_type`
- `feed`
- `title`
- `link`
- `summary`
- `published`
- `tags`
- `article_key`

They are appended alongside RSS-derived journal items in `journal_articles.json`.

### Research news

There was no existing research-news ingest file, so a minimal analogous snapshot was added:

- `generated_at`
- `items`

Each item uses the closest existing ingest conventions:

- `source_type`
- `source`
- `title`
- `link`
- `summary`
- `published`
- `tags`

## Additive Schema Fields

The following fields were added only as extra metadata. They are non-breaking because downstream scorers ignore unknown keys:

- `gmail_label`
- `message_id`
- `email_from`

Journal email items also preserve:

- `article_key`

for compatibility with existing journal dedup/state handling.

The raw bridge snapshot `email_messages.json` also contains bridge-specific fields such as:

- `mailbox`
- `route_name`
- `message_key`
- `imap_uid`
- `body_text`
- `body_html_text`
- `links`

These remain in the intermediate email snapshot and are not required by downstream scorers.

## Downstream Modules Requiring Changes

Very little downstream logic changed.

### Changed ingest adapters

- `power-tools/ingest/grant_opportunities.py`
  - Added an adapter that reads `email_messages.json` and maps grant-labeled email messages into the existing grant snapshot.
- `power-tools/ingest/rss_journals.py`
  - Added an adapter that reads `email_messages.json` and maps journal-labeled email messages into the existing journal snapshot.
- `power-tools/ingest/research_news.py`
  - Added a minimal new ingest snapshot for research news because no existing news ingest file existed.

### Unchanged scorers

- `power-tools/processing/score_grants.py`
  - No contract change needed. It still reads `grant_opportunities.json`.
- `power-tools/processing/score_articles.py`
  - No contract change needed. It still reads `journal_articles.json`.

## Small Compatibility Accommodations

Two small target-name accommodations were added so config routing can stay explicit without breaking the existing adapters:

- Journal adapter accepts route targets `journals` and `journal_articles`.
- Grant adapter accepts route targets `grants` and `grant_opportunities`.

No scoring prompt or output schema changes were required for those sources.
