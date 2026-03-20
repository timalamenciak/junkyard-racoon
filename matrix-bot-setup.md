# Matrix Bot Setup: Hybrid Cron + Nio Daemon

A lightweight, production-ready Matrix bot architecture combining cron-driven automations (RSS filtering, job/grant searching) with an interactive nio daemon for Qwen/SmolLLM chat access. No maubot complexity.

## Architecture Overview

- **Cron scripts** (~50 lines each): Handle RSS feeds, job searches, grant alerts → pipe to Matrix
- **matrix-commander**: Simple CLI tool for sending messages to Matrix rooms
- **Nio daemon**: Single Python service listening for chat messages → calls Qwen endpoint → sends responses
- **systemd service**: Manages nio daemon lifecycle

**Why this works:**
- Minimal dependencies
- Easy to debug (logs are just stdout/stderr)
- No complex SDK state management
- Cron is your scheduler; systemd is your daemon manager
- Scales well (nio handles maybe 10-20 concurrent users easily)

---

## Prerequisites

Ensure you have on your Lab Commons Nibi instance:

- Python 3.10+
- Your Qwen endpoint running (either Open WebUI or vLLM directly)
- Matrix homeserver (Synapse) already configured
- A Matrix user account for the bot (e.g., `@bot:your-domain.com`)
- SSH access to Nibi instance

---

## Step 1: Install matrix-commander

`matrix-commander` is a CLI tool that sends Matrix messages without needing a daemon.

```bash
pip install matrix-commander --break-system-packages
```

Verify:
```bash
matrix-commander --version
```

---

## Step 2: Configure matrix-commander

Create a config file to authenticate with your homeserver:

```bash
mkdir -p ~/.config/matrix-commander
cat > ~/.config/matrix-commander/config.json << 'EOF'
{
  "homeserver": "https://your-matrix-domain.com",
  "user_id": "@bot:your-matrix-domain.com",
  "device_id": "BotDevice",
  "room_id": "!roomid:your-matrix-domain.com",
  "pickle_key": "your-encryption-key"
}
EOF
```

First-time login:

```bash
matrix-commander --login password
# Enter bot username and password when prompted
# This generates device keys in ~/.config/matrix-commander/
```

Test it:

```bash
matrix-commander --text-plain "Bot is online!"
```

---

## Step 3: Set Up Nio Daemon

The nio daemon listens for messages in Matrix rooms and responds with LLM outputs.

### 3.1 Create the daemon script

**File:** `/opt/lab-commons/matrix-bot-daemon.py`

```python
#!/usr/bin/env python3
"""
Matrix Nio Daemon
Listens for messages in a room, calls Qwen endpoint, sends responses.
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

import httpx
from nio import (
    AsyncClient,
    MatrixRoom,
    RoomMessageText,
    LoginResponse,
    SyncResponse,
)

# Configuration
HOMESERVER = os.getenv("MATRIX_HOMESERVER", "https://your-matrix-domain.com")
USER_ID = os.getenv("MATRIX_USER_ID", "@bot:your-matrix-domain.com")
PASSWORD = os.getenv("MATRIX_PASSWORD", "")
ROOM_ID = os.getenv("MATRIX_ROOM_ID", "!roomid:your-matrix-domain.com")
QWEN_ENDPOINT = os.getenv("QWEN_ENDPOINT", "http://localhost:8000/v1/chat/completions")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen")
SYNC_TIMEOUT = 30000  # 30 seconds

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/var/log/matrix-bot-daemon.log"),
    ],
)
logger = logging.getLogger("matrix-bot-daemon")

client = AsyncClient(HOMESERVER, USER_ID)


async def call_qwen(prompt: str, max_tokens: int = 500) -> str:
    """Call Qwen endpoint and return response."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            response = await http_client.post(
                QWEN_ENDPOINT,
                json={
                    "model": MODEL_NAME,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.7,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Qwen API error: {e}")
        return f"Error calling LLM: {str(e)}"


async def message_callback(room: MatrixRoom, event: RoomMessageText) -> None:
    """Handle incoming messages."""
    # Ignore messages from the bot itself
    if event.sender == USER_ID:
        return

    logger.info(f"Message from {event.sender} in {room.name}: {event.body}")

    # Simple prefix check: only respond to messages starting with "!"
    if not event.body.startswith("!"):
        return

    prompt = event.body[1:].strip()  # Remove "!" prefix
    if not prompt:
        return

    # Show typing indicator (optional)
    await client.room_typing(room.id, typing_state=True)

    try:
        response_text = await call_qwen(prompt)
    except Exception as e:
        response_text = f"Error: {str(e)}"
    finally:
        await client.room_typing(room.id, typing_state=False)

    # Send response
    try:
        await client.room_send(
            room_id=room.id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": response_text,
            },
        )
        logger.info(f"Sent response to {room.name}")
    except Exception as e:
        logger.error(f"Failed to send message: {e}")


async def sync_loop() -> None:
    """Run the sync loop and handle messages."""
    client.add_event_callback(message_callback, RoomMessageText)

    logger.info("Starting sync loop...")
    try:
        await client.sync_forever(timeout=SYNC_TIMEOUT, full_state=False)
    except Exception as e:
        logger.error(f"Sync loop error: {e}")
    finally:
        await client.close()


async def main() -> None:
    """Main entry point."""
    if not PASSWORD:
        logger.error("MATRIX_PASSWORD environment variable not set")
        sys.exit(1)

    logger.info(f"Logging in as {USER_ID}")
    login_response = await client.login(PASSWORD)

    if isinstance(login_response, LoginResponse):
        logger.info(f"Login successful. Device ID: {login_response.device_id}")
    else:
        logger.error(f"Login failed: {login_response}")
        sys.exit(1)

    # Join room if needed
    try:
        await client.join(ROOM_ID)
        logger.info(f"Joined room {ROOM_ID}")
    except Exception as e:
        logger.warning(f"Could not join room: {e}")

    # Start sync loop
    await sync_loop()


if __name__ == "__main__":
    asyncio.run(main())
```

Install dependencies:

```bash
pip install matrix-nio httpx --break-system-packages
```

### 3.2 Create systemd service file

**File:** `/etc/systemd/system/matrix-bot-daemon.service`

```ini
[Unit]
Description=Matrix Bot Daemon (Nio)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=lab-commons
WorkingDirectory=/opt/lab-commons
ExecStart=/usr/bin/python3 /opt/lab-commons/matrix-bot-daemon.py

# Environment variables
Environment="MATRIX_HOMESERVER=https://your-matrix-domain.com"
Environment="MATRIX_USER_ID=@bot:your-matrix-domain.com"
Environment="MATRIX_PASSWORD=your-bot-password"
Environment="MATRIX_ROOM_ID=!roomid:your-matrix-domain.com"
Environment="QWEN_ENDPOINT=http://localhost:8000/v1/chat/completions"
Environment="MODEL_NAME=qwen"

# Restart policy
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Set permissions:

```bash
sudo chmod 644 /etc/systemd/system/matrix-bot-daemon.service
sudo systemctl daemon-reload
```

Start the daemon:

```bash
sudo systemctl start matrix-bot-daemon
sudo systemctl enable matrix-bot-daemon
```

Check status:

```bash
sudo systemctl status matrix-bot-daemon
sudo journalctl -u matrix-bot-daemon -f
```

---

## Step 4: Set Up Cron Automations

Create Python scripts for your automations, then schedule them with cron.

### 4.1 RSS Feed Filtering Script

**File:** `/opt/lab-commons/cron-jobs/rss-filter.py`

```python
#!/usr/bin/env python3
"""
Daily RSS Feed Filtering
Fetch RSS feeds, filter by keywords, output results for matrix-commander.
"""

import feedparser
from datetime import datetime, timedelta
import sys

RSS_FEEDS = [
    "https://example.com/feed.xml",
    "https://another-source.com/rss",
]

KEYWORDS = ["ecology", "restoration", "grassland", "systematic review"]

def filter_feed(url: str) -> list:
    """Fetch and filter RSS feed."""
    try:
        feed = feedparser.parse(url)
        results = []
        
        for entry in feed.entries[:10]:  # Last 10 entries
            title = entry.get("title", "")
            link = entry.get("link", "")
            
            # Check if any keyword matches
            if any(kw.lower() in title.lower() for kw in KEYWORDS):
                results.append(f"• {title}\n  {link}")
        
        return results
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return []

def main():
    all_results = []
    
    for feed_url in RSS_FEEDS:
        results = filter_feed(feed_url)
        all_results.extend(results)
    
    if all_results:
        output = "📰 **Daily RSS Digest**\n\n" + "\n\n".join(all_results)
        print(output)
    else:
        print("📰 No matching articles today.")

if __name__ == "__main__":
    main()
```

Install dependency:

```bash
pip install feedparser --break-system-packages
```

### 4.2 Job Search Script

**File:** `/opt/lab-commons/cron-jobs/job-search.py`

```python
#!/usr/bin/env python3
"""
Daily Job Search
Query job boards (e.g., Academic Positions, HigherEdJobs).
This is a stub—customize with actual API calls or web scraping.
"""

import sys
from datetime import datetime

KEYWORDS = ["postdoc", "ecology", "computational", "Canada"]

def search_jobs() -> list:
    """
    Placeholder for job search logic.
    In practice, you'd query APIs or scrape job boards.
    """
    # Example: Call OpenAlex or job board API
    results = [
        "🔍 Faculty Position: Computational Ecology (U of T)",
        "🔍 Postdoc: Grassland Restoration (Carleton)",
        "🔍 Research Scientist: Knowledge Graphs (NCC)",
    ]
    return results

def main():
    results = search_jobs()
    
    if results:
        output = "💼 **Today's Job Postings**\n\n" + "\n".join(results)
        print(output)
    else:
        print("💼 No matching positions today.")

if __name__ == "__main__":
    main()
```

### 4.3 Grant Search Script

**File:** `/opt/lab-commons/cron-jobs/grant-search.py`

```python
#!/usr/bin/env python3
"""
Daily Grant Opportunity Search
Query grant databases (e.g., Grants.ca, NSERC, SSHRC).
"""

def search_grants() -> list:
    """
    Placeholder for grant search logic.
    Query NSERC Alliance, SSHRC IDG, etc.
    """
    results = [
        "💰 NSERC Alliance Grant (Deadline: 2026-05-15)",
        "💰 SSHRC Insight Development (Deadline: 2026-07-01)",
        "💰 CFI Innovation Fund (Deadline: 2026-06-30)",
    ]
    return results

def main():
    results = search_grants()
    
    if results:
        output = "💰 **Active Grant Opportunities**\n\n" + "\n".join(results)
        print(output)
    else:
        print("💰 No new opportunities today.")

if __name__ == "__main__":
    main()
```

Make scripts executable:

```bash
chmod +x /opt/lab-commons/cron-jobs/*.py
```

### 4.4 Add to Crontab

```bash
crontab -e
```

Add these lines (adjust times as needed):

```bash
# Daily RSS filtering at 8:00 AM
0 8 * * * /opt/lab-commons/cron-jobs/rss-filter.py | matrix-commander --text-plain

# Daily job search at 9:00 AM
0 9 * * * /opt/lab-commons/cron-jobs/job-search.py | matrix-commander --text-plain

# Daily grant search at 10:00 AM
0 10 * * * /opt/lab-commons/cron-jobs/grant-search.py | matrix-commander --text-plain
```

Verify cron jobs are scheduled:

```bash
crontab -l
```

---

## Step 5: Usage

### Interactive Chat (Nio Daemon)

In your Matrix room, send a message with the `!` prefix:

```
!What are the top grassland restoration techniques?
```

The daemon will call your Qwen endpoint and reply:

```
Grassland restoration involves several key techniques:
1. Seed collection and native plant propagation...
```

### Automated Messages (Cron + matrix-commander)

Messages will appear in your configured room at scheduled times:

```
📰 **Daily RSS Digest**

• Novel approach to ecosystem restoration
  https://example.com/article1

• Systematic review of grassland interventions
  https://example.com/article2
```

---

## Troubleshooting

### Daemon won't start

Check logs:
```bash
sudo journalctl -u matrix-bot-daemon -n 50
```

Common issues:
- **Authentication failed**: Verify `MATRIX_PASSWORD` is correct
- **Connection refused**: Check Matrix homeserver URL
- **Qwen endpoint unreachable**: Verify `QWEN_ENDPOINT` and firewall rules

### Cron jobs not running

Check cron logs:
```bash
grep CRON /var/log/syslog
```

Ensure scripts are executable:
```bash
ls -l /opt/lab-commons/cron-jobs/
```

### Message not sent to Matrix

Test matrix-commander directly:
```bash
matrix-commander --text-plain "Test message"
```

Check room permissions (bot must have send-message rights).

---

## Configuration Reference

### Environment Variables (Nio Daemon)

| Variable | Default | Description |
|----------|---------|-------------|
| `MATRIX_HOMESERVER` | `https://your-matrix-domain.com` | Your Matrix homeserver URL |
| `MATRIX_USER_ID` | `@bot:your-matrix-domain.com` | Bot's user ID |
| `MATRIX_PASSWORD` | `` | Bot account password |
| `MATRIX_ROOM_ID` | `!roomid:your-matrix-domain.com` | Room where bot listens |
| `QWEN_ENDPOINT` | `http://localhost:8000/v1/chat/completions` | Qwen API endpoint |
| `MODEL_NAME` | `qwen` | Model name for API calls |

### Message Prefix

By default, the daemon only responds to messages starting with `!`. Edit `message_callback()` in the daemon script to change this behavior.

---

## Security Notes

1. **Store passwords securely**: Use systemd service file or environment variables, never hardcode
2. **Restrict room access**: Only invite trusted users to the bot's room
3. **Rate limiting**: Consider adding request throttling in `call_qwen()` if heavily used
4. **Encryption**: matrix-commander supports encrypted rooms; configure if needed

---

## Next Steps

- Customize RSS feeds and keywords in `rss-filter.py`
- Implement real job/grant search API calls (OpenAlex, Grants.ca, etc.)
- Add command prefixes to the nio daemon (e.g., `!search`, `!summarize`)
- Set up alerting for daemon restarts (e.g., Matrix notifications)
- Monitor resource usage; scale horizontally if needed

---

## Quick Reference: Commands

**Check daemon status:**
```bash
sudo systemctl status matrix-bot-daemon
```

**View daemon logs:**
```bash
sudo journalctl -u matrix-bot-daemon -f
```

**Test matrix-commander:**
```bash
matrix-commander --text-plain "Test"
```

**Reload crontab:**
```bash
crontab -e
```

**Restart daemon:**
```bash
sudo systemctl restart matrix-bot-daemon
```
