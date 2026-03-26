# Adding Ingest Sources

This is the fast path for adding a new source to `junkyard-racoon`.

There are two common routes:

1. add a source through email routing
2. add a source by editing YAML for an existing ingester

Use email when the source arrives as newsletters or alerts. Use YAML when the source already matches an RSS-based ingester or another feed-driven input.

## Option 1: Add A Source Through Email

This is the right path for job newsletters, journal TOCs, grant alerts, and news digests.

### Step 1: Create Or Reuse A Gmail Label

Create a label like:

- `jobs`
- `journals`
- `news`
- `grants`

Then add a Gmail filter so matching mail is assigned that label automatically.

### Step 2: Map The Label In `email_ingest.yaml`

Edit your local `power-tools/configs/email_ingest.yaml`:

```yaml
email_ingest:
  labels:
    - jobs

routing:
  email_label_map:
    jobs: job_openings
```

If the label is already present, you only need to add or confirm the route mapping.

### Step 3: Make Sure The Target Exists

The target name should exist in:

- `power-tools/common/email_source_registry.py`

Examples already in the repo:

- `grant_opportunities`
- `journal_articles`
- `news_items`
- `job_openings`

If your source fits one of those existing targets, that may be enough.

### Step 4: Add Parser Logic Only If Needed

If the email format is new and needs custom parsing, add a helper under:

- `power-tools/common/`

Examples:

- `power-tools/common/news_email_parser.py`
- `power-tools/common/job_email_parser.py`

Then update or add the matching ingest adapter under:

- `power-tools/ingest/`

Examples:

- `power-tools/ingest/research_news.py`
- `power-tools/ingest/job_openings.py`

## Option 2: Add A Source By Editing YAML

This is the right path when the source is already supported by an ingester, especially for RSS feeds.

### Add A Journal Feed

Edit:

- `power-tools/configs/journals.yaml`

### Add A Grant Feed

Edit:

- `power-tools/configs/grants.yaml`

### Add A News Feed

Edit:

- `power-tools/configs/news.yaml`

Typical feed pattern:

```yaml
feeds:
  - name: Example Feed
    url: https://example.org/feed.xml
    tags:
      - restoration
      - biodiversity
```

Typical grants pattern:

```yaml
sources:
  - name: Example Grants
    type: rss
    url: https://example.org/grants.xml
    tags:
      - conservation
```

## Which Route Should I Use?

Use email if:

- the source is a newsletter
- the source is a mailing list or digest
- the source does not provide stable RSS

Use YAML if:

- the ingester already supports that source type
- the source has a clean RSS feed
- you only need to add another feed URL

## After You Add A Source

Run a quick verification pass:

```powershell
py -3 power-tools\ingest\gmail_imap_bridge.py --test
py -3 power-tools\ingest\research_news.py --test
py -3 power-tools\ingest\job_openings.py --test
py -3 power-tools\nightly_run.py --test
```

Then check:

- `power-tools/data/ingest/`
- `power-tools/data/output/daily_digest.json`
- `power-tools/data/output/static_digest_site/index.html`

## Related Docs

- `docs/email_ingest_setup.md`
- `docs/adding_email_parsers.md`
