# Email Ingestion Setup

## Why Email Ingestion Was Added

`junkyard-racoon` already ingests RSS feeds for journals and grant opportunities, but some important inputs now arrive primarily by email:

- Pivot-RP grant alerts
- journal table-of-contents and latest-issue emails
- research news digests
- publisher newsletters and multi-link digests

The email ingestion upgrade lets those messages enter the same `power-tools` ingest pipeline as RSS-derived records instead of living in a separate manual review workflow.

## How It Fits the Existing Architecture

The design keeps the existing staged pipeline:

1. ingest scripts write JSON snapshots under `power-tools/data/ingest/`
2. processing scripts read those snapshots and score or summarize them
3. output scripts build final reports and digests

Email does not replace RSS. It adds Gmail IMAP as another upstream source:

- `power-tools/ingest/gmail_imap_bridge.py`
  - fetches labeled Gmail messages via IMAP
  - writes intermediate email records to `power-tools/data/ingest/email_messages.json`
- existing or analogous ingest adapters then normalize those records into the same downstream ingest outputs:
  - `power-tools/ingest/grant_opportunities.py`
  - `power-tools/ingest/rss_journals.py`
  - `power-tools/ingest/research_news.py`

That keeps the current architecture intact and lets downstream scoring stay mostly unchanged.

## Recommended Gmail Setup

Create a dedicated Gmail account for ingestion rather than using a personal inbox directly.

Suggested pattern:

- account name like `racoonlab.ingest@gmail.com`
- used only for subscriptions, alerts, and machine-readable newsletters
- no personal correspondence
- 2-Step Verification enabled
- Gmail filters and labels dedicated to routing messages into `junkyard-racoon`

This keeps the IMAP input stream clean and makes the filter rules easier to audit.

## Gmail Labels To Create

Create these labels in Gmail:

- `pivot`
- `grants`
- `journals`
- `news`

How to create them:

1. Open Gmail in the browser.
2. In the left sidebar, click `More`.
3. Click `Create new label`.
4. Create each of the labels above exactly as written.

These labels are first-pass routing metadata for `junkyard-racoon`.

Current routing meaning:

- `pivot` -> Pivot-style grant alert parsing
- `grants` -> general grant alert parsing
- `journals` -> journal article/newsletter parsing
- `news` -> research-news parsing

## Gmail Filters To Auto-Label Incoming Messages

Set Gmail filters so incoming mail is labeled automatically before IMAP fetches it.

Examples:

### Pivot

Use when messages come from Pivot-RP or ProQuest funding alerts.

Suggested filter:

- `From`: `pivot` or sender address patterns used by Pivot-RP
- Action: `Apply the label` -> `pivot`
- Optional: `Skip the Inbox`

### Grants

Use for non-Pivot grant newsletters, sponsor bulletins, or funding digests.

Suggested filter:

- `From`: known funders, grant portals, sponsor newsletters
- Or subject contains terms like `funding opportunity`, `grant alert`, `call for proposals`
- Action: `Apply the label` -> `grants`

### Journals

Use for TOC emails, latest issue alerts, and publisher digests.

Suggested filter:

- `From`: Wiley, Springer, Elsevier, Nature, BES, ESA, journal alert senders
- Or subject contains `table of contents`, `latest issue`, `new issue`, `issue alert`
- Action: `Apply the label` -> `journals`

### News

Use for research-relevant environmental or policy news digests.

Suggested filter:

- `From`: Mongabay, The Narwhal, policy/newsletter senders, environmental news sources
- Or subject contains `news digest`, `research news`, `environment news`
- Action: `Apply the label` -> `news`

Recommended Gmail filter options:

- apply the label
- optionally skip the inbox
- do not mark as important unless you want Gmail priority inbox to keep showing them

The server treats Gmail labels as the primary routing signal. Sender and subject heuristics in `junkyard-racoon` are only backup filters inside an already-selected label/mailbox.

## Gmail App Password Setup

`junkyard-racoon` uses Gmail IMAP, so you should use an app password instead of your normal Gmail password.

Steps:

1. Sign in to the dedicated Gmail account.
2. Open Google Account settings.
3. Turn on `2-Step Verification` if it is not already enabled.
4. In Google Account security settings, open `App passwords`.
5. Create a new app password for mail access.
6. Copy the generated 16-character password.

Use that app password as the IMAP password for `junkyard-racoon`.

## Required Environment Variables

The current `email_ingest.yaml` example is configured to read these environment variables:

- `JUNKYARD_GMAIL_USERNAME`
- `JUNKYARD_GMAIL_APP_PASSWORD`

Example PowerShell session:

```powershell
$env:JUNKYARD_GMAIL_USERNAME = "racoonlab.ingest@gmail.com"
$env:JUNKYARD_GMAIL_APP_PASSWORD = "your-16-char-app-password"
```

If you run `junkyard-racoon` from cron, systemd, Task Scheduler, or another wrapper, make sure those environment variables are set in that execution context too.

## Config Files Involved

Email routing is configured in:

- `power-tools/configs/email_ingest.yaml`

Start from:

- `power-tools/configs/email_ingest.yaml.example`

Current shape:

```yaml
email_ingest:
  enabled: true
  provider: gmail_imap
  host: imap.gmail.com
  port: 993
  username_env: JUNKYARD_GMAIL_USERNAME
  password_env: JUNKYARD_GMAIL_APP_PASSWORD
  labels:
    - pivot
    - grants
    - journals
    - news
  lookback_days: 14
  max_messages_per_label: 50
  unread_only: false

routing:
  email_label_map:
    pivot: grant_opportunities
    grants: grant_opportunities
    journals: journal_articles
    news: news_items
```

## How Nightly Pipeline Handling Works Now

The existing nightly runner is still:

```powershell
py -3 power-tools\nightly_run.py
```

The current high-level flow is:

1. ingest collaborator publications
2. ingest Gmail email via IMAP
3. ingest journals with RSS + email merge
4. ingest grants with RSS + email merge
5. ingest research news with RSS + email merge
6. run existing scoring and digest/output steps

Email is fetched first into an intermediate snapshot, then the journal/grant/news ingesters merge and dedupe email-derived items with RSS-derived items before downstream processing runs.

The Gmail step is intentionally non-fatal in the nightly runner:

- if Gmail IMAP is unavailable, the pipeline logs a warning and continues
- RSS ingestion and downstream scoring still run

## Output Files Affected

Intermediate email snapshot:

- `power-tools/data/ingest/email_messages.json`

Normalized ingest outputs affected by email:

- `power-tools/data/ingest/grant_opportunities.json`
  - receives RSS grants plus `pivot` / `grants` email-derived grant records
- `power-tools/data/ingest/journal_articles.json`
  - receives RSS journal articles plus `journals` email-derived article records
- `power-tools/data/ingest/news_items.json`
  - receives RSS research news plus `news` email-derived news records

Existing downstream processing still reads the same core files:

- `power-tools/processing/score_grants.py` reads `grant_opportunities.json`
- `power-tools/processing/score_articles.py` reads `journal_articles.json`

## How Dedupe Works Across Email And RSS

Cross-source dedupe happens in the ingest merge layer, not in a separate reconciliation job.

Current behavior:

- existing journal seen-state logic is preserved
- email and RSS records are normalized first
- records are deduped across sources using a deterministic fingerprint based on:
  - normalized title
  - canonical URL
  - normalized date when available

URL canonicalization removes common tracking noise such as `utm_*` query parameters.

When the same logical item appears from both sources:

- one merged item is written downstream
- provenance is preserved, for example:
  - `["rss", "email"]`
- source list is preserved, for example:
  - `["Restoration Ecology", "journals"]`
- richer metadata is filled in conservatively
- weaker metadata does not overwrite better existing values

Examples:

- an RSS article plus the same TOC item from a publisher email becomes one record in `journal_articles.json`
- an RSS news article plus the same story in a news digest becomes one record in `news_items.json`

## Practical Verification

Useful commands:

```powershell
py -3 power-tools\ingest\gmail_imap_bridge.py --test
py -3 power-tools\ingest\rss_journals.py --test
py -3 power-tools\ingest\grant_opportunities.py --test
py -3 power-tools\ingest\research_news.py --test
py -3 power-tools\nightly_run.py --test
```

What to look for:

- Gmail route logs showing label-to-parser selection
- per-step counts such as `rss=...`, `email=...`, `merged=...`
- `email_messages.json` written successfully
- merged records appearing in `journal_articles.json`, `grant_opportunities.json`, and `news_items.json`

## Recommended Operating Pattern

For day-to-day use:

- keep Gmail labels as the first-pass classification system
- keep `junkyard-racoon` parsing conservative
- prefer partial structured records over aggressive inference
- review filters occasionally when senders or newsletter formats change

That keeps Gmail responsible for broad routing and keeps `junkyard-racoon` focused on deterministic parsing, merging, dedupe, and downstream reporting.
