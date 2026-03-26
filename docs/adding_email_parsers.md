# Adding A New Email Parser

This email-ingestion cleanup keeps the extension path small on purpose. There is no separate framework to learn.

## Current Pattern

Email handling in `junkyard-racoon` is split into three lightweight layers:

1. `power-tools/ingest/gmail_imap_bridge.py`
   - fetches labeled Gmail messages via IMAP
   - writes raw normalized email records to `power-tools/data/ingest/email_messages.json`
2. parser helpers under `power-tools/common/`
   - parse a specific email style such as Pivot alerts, journal newsletters, or news digests
3. ingest adapters under `power-tools/ingest/`
   - map parsed email content into the existing downstream JSON snapshot for that source

## Small Registry

The only shared routing abstraction is:

- `power-tools/common/email_source_registry.py`

It centralizes:

- label aliases
- target aliases
- parser-name lookup for logging

This is intentionally small. If you add a new email source later, start there.

## Add A New Email Source In A Few Steps

Example future sources:

- collaborator publication alerts
- government or policy newsletters
- another grant newsletter family
- more publisher TOC formats

Recommended steps:

1. Add a routing entry in `power-tools/common/email_source_registry.py`.
   - add the canonical target name
   - add any Gmail label aliases
   - add any accepted target aliases

2. Update `power-tools/configs/email_ingest.yaml.example`.
   - add the Gmail label to `email_ingest.labels` if needed
   - add the `routing.email_label_map` target

3. Add a parser helper in `power-tools/common/` only if the email format needs structure-aware parsing.
   - examples:
     - `pivot_email_parser.py`
     - `journal_email_parser.py`
     - `news_email_parser.py`

4. Update or add the corresponding ingest adapter in `power-tools/ingest/`.
   - read `email_messages.json`
   - use `route_matches_target(...)`
   - parse only the messages for that target
   - map records into the existing downstream JSON shape if possible
   - keep additive metadata non-breaking

5. Add a small pytest file and, if needed, one readable HTML fixture under `power-tools/tests/fixtures/`.

6. If the new source needs to run nightly, wire it into the existing `power-tools/nightly_run.py` flow rather than creating a second runner.

## Keep It Modest

When adding another source, prefer:

- one parser helper
- one ingest adapter update
- one registry entry
- one or two focused tests

Avoid:

- generic plugin systems
- runtime parser discovery
- large inheritance hierarchies
- separate orchestration paths

The current design works best when Gmail handles first-pass labeling and `junkyard-racoon` handles conservative second-pass parsing and merging.
