# Junkyard Racoon

A nightly research intelligence pipeline for the Racoon Lab at Wilfrid Laurier University. It ingests journal articles, grants, news, and job postings; scores them with an LLM for lab relevance; and publishes a daily digest as a static HTML site, a HedgeDoc wiki, and a Matrix room post.

---

## How it works

```
ingest → score (LLM) → daily digest → static HTML + HedgeDoc + Matrix + Mastodon toots
```

Each night `power-tools/nightly_run.py` runs all pipeline steps in order. Steps that touch external services (Gmail, Matrix) are marked `allow_failure` so one outage doesn't break the whole run.

---

## Prerequisites

- Python 3.10+
- pip packages: `pip install pyyaml requests matrix-nio feedparser`
- An LLM endpoint (local or API) — see [Configuring the LLM](#configuring-the-llm)
- Obsidian vault synced to the server (for the tasks pipeline) — see [Syncing the Obsidian vault](#syncing-the-obsidian-vault)

---

## Quick start

```bash
# 1. Clone
git clone <repo-url>
cd junkyard-racoon

# 2. Install dependencies
pip install pyyaml requests matrix-nio feedparser

# 3. Copy and edit config files
cp power-tools/configs/llm.yaml.example      power-tools/configs/llm.yaml
cp power-tools/configs/lab_profile.yaml      # edit in place — already has defaults
cp power-tools/configs/output.yaml.example   power-tools/configs/output.yaml
cp power-tools/configs/email_ingest.yaml.example power-tools/configs/email_ingest.yaml

# 4. Run a safe test (no external calls, generates sample data)
python power-tools/nightly_run.py --test

# 5. Check output
ls power-tools/data/output/
```

---

## Config files

All configs live in `power-tools/configs/`. None of these are committed — copy from the `.example` files and edit locally.

| File | What it controls |
|------|-----------------|
| `llm.yaml` | LLM endpoint, model, API key, timeout |
| `lab_profile.yaml` | Lab name, research interests, relevance thresholds, Obsidian vault path |
| `output.yaml` | Static site URL/path, HedgeDoc URL and token, Matrix digest filename |
| `email_ingest.yaml` | Gmail IMAP connection, label routing, lookback window |
| `journals.yaml` | RSS feeds to ingest for journal articles, with topic tags |
| `news.yaml` | RSS feeds for research news, with keyword scoring |
| `grants.yaml` | RSS feeds for grant opportunities |
| `jobs.yaml` | Job board URLs to scrape (GoodWork, University Affairs, etc.) |
| `collaborators.yaml` | ORCID IDs to monitor for new publications |
| `manual_grants.yaml` | Grants you are manually tracking — always surfaced in digest regardless of LLM score. Copy from `manual_grants.yaml.example`. |

### Configuring the LLM

`power-tools/configs/llm.yaml`:

```yaml
provider: local            # local | openai_compatible | groq
endpoint: http://racoon-ai:8080/v1/chat/completions
api_key: your-key-here     # omit or leave blank for unauthenticated local endpoints
model: qwen2.5-3b
timeout: 900
```

The same file is used by all pipeline scripts **and** by the Matrix bot daemon — no duplication needed.

---

## Environment variables

### Gmail ingest

The variable names are set in `email_ingest.yaml` under `username_env` and `password_env`. The defaults are:

| Variable | Description |
|----------|-------------|
| `JUNKYARD_GMAIL_USERNAME` | Gmail address used for IMAP |
| `JUNKYARD_GMAIL_APP_PASSWORD` | Gmail [App Password](https://support.google.com/accounts/answer/185833) (not your main password) |

### Matrix bot

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MATRIX_HOMESERVER` | Yes | — | Your Matrix server URL, e.g. `https://matrix.example.com` |
| `MATRIX_USER_ID` | Yes | — | Bot account user ID, e.g. `@bot:example.com` |
| `MATRIX_PASSWORD` | Yes (first run) | — | Bot account password. Only needed until credentials are cached |
| `MATRIX_ROOM_ID` | Yes | — | Room where the digest is posted, e.g. `!abc123:example.com` |
| `CREDS_FILE` | No | `.credentials.json` (repo root) | Where to cache the Matrix session token |
| `MATRIX_BOT_CONFIG` | No | `matrix-bot-daemon.yaml` (repo root) | Path to daemon YAML config |
| `MATRIX_DIGEST_FILE` | No | `power-tools/data/output/matrix_digest.txt` | Digest file the bot reads for `!digest` and nightly posts |
| `MATRIX_BOT_LOG` | No | `matrix-bot-daemon.log` (repo root) | Log file path |
| `COMMANDS_DIR` | No | `/home/ubuntu/junkyard-racoon/tools` | Directory of plug-in command scripts |

After the first successful login the session token is written to `CREDS_FILE`. Subsequent runs restore the session without needing `MATRIX_PASSWORD`.

---

## Running the pipeline

**Test run** (no external calls, safe to run anywhere):

```bash
python power-tools/nightly_run.py --test
```

**Production run:**

```bash
python power-tools/nightly_run.py
```

Individual steps can be run in isolation for debugging:

```bash
python power-tools/ingest/rss_journals.py
python power-tools/processing/score_articles.py
python power-tools/output/publish_static_digest.py
```

---

## Deployment

### systemd (Linux, recommended)

Copy the service and timer files, then enable:

```bash
sudo cp deploy/junkyard-racoon-nightly.service /etc/systemd/system/
sudo cp deploy/junkyard-racoon-nightly.timer    /etc/systemd/system/

# Edit the service file to set WorkingDirectory and environment variables
sudo nano /etc/systemd/system/junkyard-racoon-nightly.service

sudo systemctl daemon-reload
sudo systemctl enable --now junkyard-racoon-nightly.timer

# Check status
sudo systemctl status junkyard-racoon-nightly.timer
sudo journalctl -u junkyard-racoon-nightly -f
```

The timer fires at **06:15 daily** by default (edit `OnCalendar=` in the `.timer` file to change this).

Add environment variables to the service file using `Environment=` lines:

```ini
[Service]
Environment="JUNKYARD_GMAIL_USERNAME=lab@example.com"
Environment="JUNKYARD_GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx"
Environment="MATRIX_HOMESERVER=https://matrix.example.com"
Environment="MATRIX_USER_ID=@bot:example.com"
Environment="MATRIX_PASSWORD=hunter2"
Environment="MATRIX_ROOM_ID=!abc123:example.com"
```

Or point to an env file with `EnvironmentFile=/etc/junkyard-racoon.env`.

### cron (alternative)

```bash
# Edit crontab
crontab -e

# Add (adjust path and time as needed):
15 6 * * * cd /opt/junkyard-racoon && python power-tools/nightly_run.py >> /var/log/junkyard-racoon.log 2>&1
```

A cron template is also in `deploy/power-tools-nightly.cron`.

### Serving the static digest

The pipeline writes the HTML site to `power-tools/data/output/static_digest_site/`. To serve it:

```bash
# Simple Python server for testing
python -m http.server 8085 --directory power-tools/data/output/static_digest_site/

# Or use the included systemd service which binds to 127.0.0.1:8085
sudo cp deploy/junkyard-racoon-digest.service /etc/systemd/system/
sudo systemctl enable --now junkyard-racoon-digest
```

Reverse-proxy port 8085 with nginx or Caddy to expose it publicly. Set the public URL in `output.yaml` under `static_site.public_url` so internal links in the HTML are correct.

---

## Matrix bot

The daemon (`matrix-bot-daemon.py`) serves two roles:

1. **Nightly proactive post** — `post_matrix_digest.py` calls the daemon with `--post-digest` at the end of each pipeline run
2. **Interactive commands** — the daemon listens for `!commands` in the configured room

**Built-in commands:**

| Command | What it does |
|---------|-------------|
| `!status` | Shows bot config, LLM source, and available commands |
| `!digest` | Posts the current `matrix_digest.txt` to the room |

**Running as a daemon:**

```bash
# Set env vars, then:
python matrix-bot-daemon.py
```

**systemd service** (for keeping it running):

```ini
# /etc/systemd/system/matrix-bot-daemon.service
[Unit]
Description=Junkyard Racoon Matrix Bot
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/junkyard-racoon
ExecStart=/usr/bin/python3 /opt/junkyard-racoon/matrix-bot-daemon.py
EnvironmentFile=/etc/junkyard-racoon.env
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

**Optional `matrix-bot-daemon.yaml`** (copy from `matrix-bot-daemon.yaml.example`):

LLM settings come from `power-tools/configs/llm.yaml` automatically. The daemon YAML is only needed if you have plug-in tool scripts with their own API credentials.

---

## Mastodon output

Each daily digest includes 5 LLM-generated toots (stored in `daily_digest.json` under `mastodon_toots`). `post_mastodon.py` posts them automatically as part of the nightly pipeline.

### One-time setup

1. **Create a Mastodon application** on your instance:
   - Go to `https://your-instance/settings/applications`
   - Click **New Application**
   - Name it something like `Junkyard Racoon Bot`
   - Scopes needed: `write:statuses` only
   - Click **Submit**, then copy the **Your access token** value

2. **Set environment variables** (same rules as Matrix — single quotes in bash, bare values in systemd `EnvironmentFile`):

   ```bash
   export MASTODON_INSTANCE_URL='https://your-instance.social'
   export MASTODON_ACCESS_TOKEN='your-token-here'
   ```

   Optional:

   | Variable | Default | Description |
   |----------|---------|-------------|
   | `MASTODON_POST_DELAY_SEC` | `30` | Seconds to wait between toots (avoids rate-limiting) |
   | `MASTODON_VISIBILITY` | `public` | `public`, `unlisted`, or `private` |

3. **Test without actually posting:**

   ```bash
   POWER_TOOLS_TEST_MODE=1 python power-tools/output/post_mastodon.py
   ```

4. **Post today's toots manually** (before enabling in the nightly run):

   ```bash
   python power-tools/output/post_mastodon.py
   ```

The script is idempotent — it tracks the last posted date in `data/state/mastodon_posted.json` and skips if today's toots have already gone out, so re-running the pipeline won't double-post.

---

## Syncing the Obsidian vault

The tasks pipeline (`obsidian_todos.py`) reads markdown files directly from your Obsidian vault. The pipeline running on the Ubuntu server cannot read `C:\Users\Tim\Obsidian\LabVault` — it needs a copy of the vault at a local Linux path.

### Option A — Syncthing (recommended, ongoing sync)

1. Install Syncthing on both your Windows machine and the server:
   ```bash
   # Server
   sudo apt install syncthing
   sudo systemctl enable --now syncthing@ubuntu
   # Then open http://racoon-services:8384 to configure the web UI
   ```
2. On Windows, install [Syncthing for Windows](https://syncthing.net/downloads/)
3. Pair the two devices and share your `LabVault` folder to `/home/ubuntu/obsidian-vault` on the server
4. Uncomment and set the server path in `lab_profile.yaml`:
   ```yaml
   obsidian_vault_paths:
     - /home/ubuntu/obsidian-vault
   ```

### Option B — rsync one-way push (simpler, pre-run)

On your Windows machine (WSL2 or Git Bash), schedule a task that runs before the 6:15 AM pipeline:

```bash
rsync -av --include="*/" --include="*.md" --exclude="*" \
  "/mnt/c/Users/Tim/Obsidian/LabVault/" \
  ubuntu@racoon-services:/home/ubuntu/obsidian-vault/
```

Or as a Windows Task Scheduler job calling `wsl rsync ...` — set it to run at 6:00 AM daily, 15 minutes before the pipeline.

### Option C — Git-backed vault

If your vault is in a private git repo, add a pull step before the pipeline:

```bash
# In the systemd service ExecStartPre, or at the top of nightly_run.py
git -C /home/ubuntu/obsidian-vault pull --quiet
```

### After syncing

Update `power-tools/configs/lab_profile.yaml`:

```yaml
obsidian_vault_paths:
  - /home/ubuntu/obsidian-vault   # server path (Linux)
  # - C:\Users\Tim\Obsidian\LabVault  # keep if also running locally on Windows

todo_project_globs:
  - Projects/**/*.md
```

The pipeline will use whichever paths actually exist on the current machine, so you can keep both and it works on both Windows and Linux.

---

## Extending the pipeline

- **New ingest source:** see `docs/adding_ingest_sources.md`
- **New email parser:** see `docs/adding_email_parsers.md`
- **New Matrix command:** drop a `your_command.py` script into `COMMANDS_DIR`; it receives arguments via stdin and should print the response to stdout

---

## Data directory layout

```
power-tools/data/
  ingest/        raw records from external sources
  processing/    scored and summarized artifacts
  output/        final deliverables (digest JSON/MD, HTML site, matrix text)
  state/         deduplication caches and rolling history
```

All data files are gitignored.
