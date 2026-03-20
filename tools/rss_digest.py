#!/usr/bin/env python3
"""
!rss-digest command — Daily academic RSS digest with LLM relevance scoring.

Workflow:
  1. Fetch RSS feeds from configured academic journals (last 24h)
  2. Batch-score articles via Qwen for relevance using a tunable prompt
  3. Post a BookStack page with summaries of relevant articles
  4. Print a Matrix-ready synopsis to stdout

Usage (Maubot stdin pipe):
  echo "" | python3 rss_digest.py
  echo "threshold=0.6" | python3 rss_digest.py   # override score threshold

Configuration:
  - edit the CONFIG block below for defaults
  - or provide env vars directly
  - or place matrix-bot-daemon.yaml beside the daemon for cron/CLI reuse
"""

import sys
import os
import json
import re
import time
import datetime
import textwrap
from email.utils import parsedate_to_datetime
from pathlib import Path
import feedparser
import urllib.request
import urllib.parse
import urllib.error

try:
    import yaml
except ImportError:
    yaml = None

# ════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════

CONFIG = {
    # ── RSS feeds ────────────────────────────────────────────────
    "feeds": [
        # Conservation Biology
        "https://conbio.onlinelibrary.wiley.com/feed/15231739/most-recent",
        # Restoration Ecology
        "https://onlinelibrary.wiley.com/feed/1526100x/most-recent",
        # Biological Conservation
        "https://rss.sciencedirect.com/publication/science/00063207",
        # Ecology and Society (open access, social-ecological)
        "https://www.ecologyandsociety.org/viewissue.php?sf=rss",
        # People and Nature (BES — social-ecological)
        "https://besjournals.onlinelibrary.wiley.com/feed/26888319/most-recent",
        # Methods in Ecology and Evolution (for AI/methods papers)
        "https://besjournals.onlinelibrary.wiley.com/feed/2041210x/most-recent",
        # Global Change Biology
        "https://onlinelibrary.wiley.com/feed/13652486/most-recent",
    ],

    # ── LLM provider ─────────────────────────────────────────────
    # Set "llm_provider" to one of:
    #   "openai_compatible"  — any OpenAI-compatible endpoint (Qwen, llama.cpp, vLLM, OpenAI, …)
    #   "claude"             — Anthropic Claude API (https://api.anthropic.com)
    #
    # Environment variable overrides (recommended for secrets):
    #   LLM_PROVIDER, LLM_URL, LLM_API_KEY, LLM_MODEL
    "llm_provider": "openai_compatible",   # "openai_compatible" | "claude"

    # OpenAI-compatible settings (used when llm_provider == "openai_compatible")
    "llm_url": "http://172.16.5.8:8080/v1/chat/completions",
    "llm_api_key": "",
    "llm_model": "qwen3-14b",              # model alias from llama.cpp / vLLM / OpenAI

    # Claude settings (used when llm_provider == "claude")
    # Set ANTHROPIC_API_KEY env var instead of putting the key here.
    "claude_api_key": "",                  # fallback if env var not set
    "claude_model": "claude-opus-4-6",     # or claude-sonnet-4-6, claude-haiku-4-5-20251001

    "llm_timeout": 300,                    # seconds
    "http_timeout": 30,                    # seconds
    "http_retries": 3,
    "retry_backoff_seconds": 2,
    "llm_max_tokens": 2000,
    "batch_size": 12,                      # articles per LLM call

    # ── Relevance scoring ────────────────────────────────────────
    # Score 0.0–1.0. Articles at or above threshold are kept.
    "relevance_threshold": 0.8,

    # Tunable system prompt — edit to shift topical focus
    "scoring_system_prompt": textwrap.dedent("""\
        You are a research relevance classifier for an ecology lab at Wilfrid Laurier University.
        The lab's core interests are:
          1. AI, semantics and machine learning applications in ecology and conservation biology
          2. Human dimension of restoration and conservation, engagement in ecology
          3. Restoration ecology, including social science aspects of restoration practice
          4. Researcher–practitioner connections, knowledge co-production, boundary work
          5. Conservation policy, community-based conservation, Indigenous-led stewardship

        For each article, assign a relevance score from 0.0 (completely irrelevant) to 1.0 (highly relevant).
        Score above 0.6 only if the article clearly intersects one of the five interest areas above.
        Return ONLY a valid JSON array — no preamble, no markdown, no commentary.
    """),

    # Appended to scoring_system_prompt only for Qwen/llama.cpp models that support it.
    # Set to "" to disable.
    "qwen_no_think_suffix": "\n        /nothink",

    # ── BookStack ────────────────────────────────────────────────
    "bookstack_url": "https://wiki.lab.tim-a.ca",
    "bookstack_token_id":     "",          # Settings → API tokens
    "bookstack_token_secret": "",
    "bookstack_book_id": 3,             # Book to post into (integer)
    "bookstack_chapter_id": None,       # Optional chapter (integer or None)

    # ── Misc ──────────────────────────────────────────────────────
    "lookback_hours": 24,
    "max_articles_per_feed": 30,        # cap before scoring
    "max_relevant_articles": 20,        # cap after scoring (top-N by score)
    "date_format": "%Y-%m-%d",
}

DAEMON_CONFIG_PATH_CANDIDATES = [
    Path(os.environ.get("MATRIX_BOT_CONFIG", "")).expanduser() if os.environ.get("MATRIX_BOT_CONFIG") else None,
    Path(__file__).resolve().parent.parent / "matrix-bot-daemon.yaml",
]
RSS_STATE_PATH = Path(__file__).resolve().parent.parent / "power-tools" / "data" / "state" / "rss_seen_articles.json"

# ════════════════════════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════════════════════════

def log(msg: str):
    sys.stderr.write(f"[rss_digest] {msg}\n")


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")

def strip_html(text: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = _HTML_TAG_RE.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def parse_date(entry) -> datetime.datetime | None:
    """Return a tz-aware UTC datetime from a feedparser entry, or None."""
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        t = getattr(entry, attr, None)
        if t:
            try:
                return datetime.datetime(*t[:6], tzinfo=datetime.timezone.utc)
            except Exception:
                pass
    for attr in ("published", "updated", "created"):
        raw = entry.get(attr)
        if raw:
            try:
                parsed = parsedate_to_datetime(raw)
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=datetime.timezone.utc)
                return parsed.astimezone(datetime.timezone.utc)
            except Exception:
                pass
    return None


def article_key(link: str, title: str, published: str) -> str:
    return "||".join((link.strip(), title.strip().lower(), published.strip()))


def load_read_state() -> set[str]:
    if not RSS_STATE_PATH.exists():
        return set()
    try:
        payload = json.loads(RSS_STATE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log(f"Could not read RSS state file: {exc}")
        return set()
    return set(payload.get("seen_article_keys", []))


def save_read_state(keys: set[str]) -> None:
    RSS_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    RSS_STATE_PATH.write_text(
        json.dumps(
            {
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "seen_article_keys": sorted(keys),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def sample_articles() -> list[dict]:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return [
        {
            "title": "Sample: Benchmarking LLM-assisted habitat classification workflows",
            "link": "https://example.org/sample-llm-habitat-classification",
            "summary": "Sample article for test mode showing an AI-enabled ecology methods paper.",
            "short_summary": "Sample article for test mode showing an AI-enabled ecology methods paper.",
            "published": now,
            "feed": "Methods in Ecology and Evolution",
            "score": 0.91,
            "reason": "Strong methods fit",
        },
        {
            "title": "Sample: Community-led restoration outcomes across urban wetlands",
            "link": "https://example.org/sample-urban-wetlands-restoration",
            "summary": "Sample article for test mode covering restoration practice and social dimensions.",
            "short_summary": "Sample article for test mode covering restoration practice and social dimensions.",
            "published": now,
            "feed": "Restoration Ecology",
            "score": 0.84,
            "reason": "Direct restoration relevance",
        },
    ]


def _extract_json_array(raw: str) -> str:
    """Extract a JSON array from a model response, allowing fenced output."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    match = _JSON_ARRAY_RE.search(text)
    if not match:
        raise ValueError("No JSON array found in model response")
    return match.group(0)


def _load_daemon_yaml_for_env() -> None:
    """Hydrate env vars from the shared daemon YAML for standalone cron/CLI runs."""
    config_path = next((path for path in DAEMON_CONFIG_PATH_CANDIDATES if path and path.exists()), None)
    if not config_path:
        return

    if yaml is None:
        raise RuntimeError(
            "matrix-bot-daemon.yaml found but PyYAML is not installed. Install it with 'pip install pyyaml'."
        )

    with config_path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}

    if not isinstance(loaded, dict):
        raise ValueError(f"Daemon config must contain a top-level mapping: {config_path}")

    llm_cfg = loaded.get("llm", {})
    command_cfg = loaded.get("commands", {}).get("rss_digest", {})
    env_mapping = {
        "LLM_PROVIDER": llm_cfg.get("provider"),
        "LLM_URL": llm_cfg.get("url"),
        "LLM_API_KEY": llm_cfg.get("api_key"),
        "LLM_MODEL": llm_cfg.get("model"),
        "ANTHROPIC_API_KEY": llm_cfg.get("claude_api_key"),
        "CLAUDE_MODEL": llm_cfg.get("claude_model"),
    }

    for env_key, value in env_mapping.items():
        if value and not os.environ.get(env_key):
            os.environ[env_key] = str(value)

    for env_key, value in command_cfg.get("env", {}).items():
        if value is not None and not os.environ.get(str(env_key)):
            os.environ[str(env_key)] = str(value)

    log(f"Loaded shared daemon config from {config_path}")


def _request_json(req: urllib.request.Request, timeout: int, retries: int | None = None) -> dict:
    """Fetch and decode a JSON response with simple retry/backoff."""
    retries = retries if retries is not None else CONFIG["http_retries"]
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
            return json.loads(body)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt >= retries:
                break
            sleep_for = CONFIG["retry_backoff_seconds"] * attempt
            log(f"  retrying request after error ({attempt}/{retries}): {exc}")
            time.sleep(sleep_for)

    raise RuntimeError(f"request failed after {retries} attempts: {last_error}")


def fetch_feeds(lookback_hours: int, read_state: set[str] | None = None) -> tuple[list[dict], set[str]]:
    """Fetch all configured feeds; return articles from the last N hours."""
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=lookback_hours)
    articles = []
    seen_keys = set()
    read_state = set(read_state or ())
    updated_state = set(read_state)

    for url in CONFIG["feeds"]:
        log(f"Fetching {url}")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "racoon-bot/1.0"})
            with urllib.request.urlopen(req, timeout=CONFIG["http_timeout"]) as resp:
                payload = resp.read()
            feed = feedparser.parse(payload)
        except Exception as e:
            log(f"  ✗ parse error: {e}")
            continue

        if getattr(feed, "bozo", False):
            log(f"  feed warning: {feed.bozo_exception}")

        count = 0
        for entry in feed.entries:
            if count >= CONFIG["max_articles_per_feed"]:
                break

            pub = parse_date(entry)
            if pub and pub < cutoff:
                continue  # too old

            title   = entry.get("title", "").strip()
            link    = entry.get("link", "").strip()
            summary = strip_html(entry.get("summary", entry.get("description", "")))
            # Truncate summary for scoring prompt
            short_summary = summary[:400] + ("..." if len(summary) > 400 else "")

            if not title:
                continue
            if not link:
                log(f"  skipping article without link: {title[:80]}")
                continue

            dedupe_key = (link, title.lower())
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            state_key = article_key(link, title, pub.isoformat() if pub else "unknown")
            if state_key in read_state:
                continue
            updated_state.add(state_key)

            articles.append({
                "title":         title,
                "link":          link,
                "summary":       summary,
                "short_summary": short_summary,
                "published":     pub.isoformat() if pub else "unknown",
                "feed":          feed.feed.get("title", url),
                "score":         0.0,
            })
            count += 1

        log(f"  → {count} articles within window")

    log(f"Total articles fetched: {len(articles)}")
    return articles, updated_state


# ════════════════════════════════════════════════════════════════
# LLM SCORING
# ════════════════════════════════════════════════════════════════

def _apply_env_overrides():
    """Apply environment variable overrides to CONFIG (called once at startup)."""
    mapping = {
        "LLM_PROVIDER": "llm_provider",
        "LLM_URL":      "llm_url",
        "LLM_API_KEY":  "llm_api_key",
        "LLM_MODEL":    "llm_model",
        "CLAUDE_MODEL": "claude_model",
        "BOOKSTACK_URL": "bookstack_url",
        "BOOKSTACK_TOKEN_ID": "bookstack_token_id",
        "BOOKSTACK_TOKEN_SECRET": "bookstack_token_secret",
    }
    for env_key, cfg_key in mapping.items():
        val = os.environ.get(env_key)
        if val:
            CONFIG[cfg_key] = val
    # Anthropic key can live in either env var
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_API_KEY")
    if anthropic_key:
        CONFIG["claude_api_key"] = anthropic_key


def _llm_request(messages: list[dict]) -> str:
    """Dispatch to the configured LLM provider; return response content string."""
    provider = CONFIG.get("llm_provider", "openai_compatible")
    if provider == "claude":
        return _llm_request_claude(messages)
    return _llm_request_openai(messages)


def _llm_request_openai(messages: list[dict]) -> str:
    """POST to an OpenAI-compatible chat/completions endpoint."""
    api_key = CONFIG.get("llm_api_key") or ""
    if not api_key:
        raise ValueError("LLM_API_KEY is required for openai_compatible provider")

    payload = json.dumps({
        "model":       CONFIG["llm_model"],
        "messages":    messages,
        "max_tokens":  CONFIG["llm_max_tokens"],
        "temperature": 0.0,
    }).encode()

    req = urllib.request.Request(
        CONFIG["llm_url"],
        data=payload,
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    data = _request_json(req, timeout=CONFIG["llm_timeout"], retries=CONFIG["http_retries"])
    return data["choices"][0]["message"]["content"]


def _llm_request_claude(messages: list[dict]) -> str:
    """POST to the Anthropic Messages API."""
    api_key = CONFIG.get("claude_api_key") or ""
    if not api_key:
        raise ValueError(
            "Claude provider selected but no API key found. "
            "Set ANTHROPIC_API_KEY environment variable or claude_api_key in CONFIG."
        )

    # Anthropic API separates the system prompt from the messages array.
    system_prompt = ""
    user_messages = []
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        else:
            user_messages.append(msg)

    payload_dict: dict = {
        "model":      CONFIG["claude_model"],
        "messages":   user_messages,
        "max_tokens": CONFIG["llm_max_tokens"],
    }
    if system_prompt:
        payload_dict["system"] = system_prompt

    payload = json.dumps(payload_dict).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type":      "application/json",
            "x-api-key":         api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    data = _request_json(req, timeout=CONFIG["llm_timeout"], retries=CONFIG["http_retries"])
    # Anthropic response: {"content": [{"type": "text", "text": "..."}], ...}
    return data["content"][0]["text"]


def score_articles(articles: list[dict], test_mode: bool = False) -> list[dict]:
    """
    Score articles in batches. Adds a 'score' float (0–1) to each dict.
    Each batch call returns a JSON array like:
      [{"index": 0, "score": 0.8, "reason": "..."}, ...]
    """
    batch_size = CONFIG["batch_size"]
    total = len(articles)

    if test_mode:
        for idx, article in enumerate(articles):
            article["score"] = round(max(0.6, 0.92 - (idx * 0.08)), 2)
            article["reason"] = "Generated in test mode"
        return articles

    for start in range(0, total, batch_size):
        batch = articles[start : start + batch_size]
        log(f"Scoring batch {start//batch_size + 1} ({len(batch)} articles)…")

        # Build the user message listing each article
        lines = []
        for i, art in enumerate(batch):
            lines.append(
                f'[{i}] Title: {art["title"]}\n'
                f'    Abstract: {art["short_summary"]}'
            )
        user_content = (
            "Score each article for relevance. "
            "Return a JSON array with one object per article: "
            '[{"index": <int>, "score": <float 0-1>, "reason": "<10 words max>"}]\n\n'
            + "\n\n".join(lines)
        )

        system_prompt = CONFIG["scoring_system_prompt"]
        # /nothink is a Qwen-specific hint; skip it for Claude and other providers.
        if CONFIG.get("llm_provider", "openai_compatible") == "openai_compatible":
            system_prompt += CONFIG.get("qwen_no_think_suffix", "")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_content},
        ]

        raw = None
        try:
            raw = _llm_request(messages)
            clean = _extract_json_array(raw)
            scored = json.loads(clean)
            if not isinstance(scored, list):
                raise ValueError("Model response was not a list")
            for item in scored:
                if not isinstance(item, dict):
                    continue
                idx = item.get("index")
                if idx is not None and 0 <= idx < len(batch):
                    score = max(0.0, min(1.0, float(item.get("score", 0.0))))
                    batch[idx]["score"]  = score
                    batch[idx]["reason"] = item.get("reason", "")
        except Exception as e:
            log(f"  ✗ scoring error: {e} — raw: {raw[:200] if raw else 'n/a'}")
            # Leave scores at 0.0 for this batch

        time.sleep(1)  # polite pause between batches

    return articles


# ════════════════════════════════════════════════════════════════
# BOOKSTACK
# ════════════════════════════════════════════════════════════════

def _bookstack_headers() -> dict:
    if not CONFIG.get("bookstack_token_id") or not CONFIG.get("bookstack_token_secret"):
        raise ValueError("BOOKSTACK_TOKEN_ID and BOOKSTACK_TOKEN_SECRET are required for BookStack posting")
    return {
        "Authorization": f"Token {CONFIG['bookstack_token_id']}:{CONFIG['bookstack_token_secret']}",
        "Content-Type":  "application/json",
    }


def build_bookstack_markdown(relevant: list[dict], date_str: str) -> str:
    """Render a BookStack-ready markdown page."""
    lines = [
        f"# Daily Literature Digest — {date_str}\n",
        f"*Auto-generated by racoon-bot · {len(relevant)} articles · "
        f"threshold ≥ {CONFIG['relevance_threshold']}*\n",
        "---\n",
    ]

    for art in relevant:
        score_pct = int(art['score'] * 100)
        lines += [
            f"## {art['title']}",
            f"**Source:** {art['feed']}  ",
            f"**Published:** {art['published'][:10]}  ",
            f"**Relevance:** {score_pct}%  ",
            f"**Link:** [{art['link']}]({art['link']})\n",
            art["summary"][:800] + ("…" if len(art["summary"]) > 800 else ""),
            "\n---\n",
        ]

    return "\n".join(lines)


def post_to_bookstack(markdown: str, date_str: str, test_mode: bool = False) -> str | None:
    """Create (or update) a BookStack page. Returns the page URL or None."""
    if test_mode:
        return f"https://example.org/books/sample/pages/literature-digest-{date_str}"
    if not CONFIG.get("bookstack_url"):
        log("BookStack disabled: no BOOKSTACK_URL configured")
        return None

    page_name = f"Literature Digest {date_str}"

    # Check if a page with this name already exists in the book
    search_url = (
        f"{CONFIG['bookstack_url']}/api/search"
        f"?query={urllib.parse.quote(page_name)}&type=page"
    )
    try:
        req = urllib.request.Request(search_url, headers=_bookstack_headers())
        results = _request_json(req, timeout=CONFIG["http_timeout"])
        pages = results.get("data", [])
        existing = next(
            (p for p in pages
             if p.get("name") == page_name and p.get("book_id") == CONFIG["bookstack_book_id"]),
            None,
        )
    except Exception as e:
        log(f"BookStack search error: {e}")
        existing = None

    payload_dict = {
        "name":      page_name,
        "markdown":  markdown,
        "book_id":   CONFIG["bookstack_book_id"],
    }
    if CONFIG.get("bookstack_chapter_id"):
        payload_dict["chapter_id"] = CONFIG["bookstack_chapter_id"]

    payload = json.dumps(payload_dict).encode()

    if existing:
        page_id  = existing["id"]
        api_url  = f"{CONFIG['bookstack_url']}/api/pages/{page_id}"
        method   = "PUT"
    else:
        api_url  = f"{CONFIG['bookstack_url']}/api/pages"
        method   = "POST"

    req = urllib.request.Request(
        api_url,
        data=payload,
        headers=_bookstack_headers(),
        method=method,
    )
    try:
        result = _request_json(req, timeout=CONFIG["http_timeout"])
        slug = result.get("slug", "")
        book_slug = result.get("book_slug", "")
        if not slug or not book_slug:
            log(f"BookStack response missing slug fields: {result}")
            return None
        return f"{CONFIG['bookstack_url']}/books/{book_slug}/pages/{slug}"
    except Exception as e:
        log(f"BookStack post error: {e}")
        return None


# ════════════════════════════════════════════════════════════════
# MATRIX OUTPUT
# ════════════════════════════════════════════════════════════════

def build_matrix_message(relevant: list[dict], page_url: str | None, date_str: str) -> str:
    """Build a concise Matrix message (Markdown)."""
    if not relevant:
        return (
            f"📰 **Literature Digest {date_str}** — "
            "No articles above relevance threshold in the last 24 hours."
        )

    lines = [
        f"📰 **Literature Digest {date_str}** — {len(relevant)} relevant articles\n"
    ]

    for art in relevant[:10]:  # cap at 10 items in the Matrix message
        score_pct = int(art["score"] * 100)
        title_short = art["title"][:90] + ("..." if len(art["title"]) > 90 else "")
        lines.append(f"**{score_pct}%** [{title_short}]({art['link']})")

    if len(relevant) > 10:
        lines.append(f"_…and {len(relevant) - 10} more_")

    if page_url:
        lines.append(f"\n📖 [Full digest on BookStack]({page_url})")
    else:
        lines.append("\n⚠️ BookStack post failed — check logs.")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    _load_daemon_yaml_for_env()
    # Apply environment variable overrides before anything else.
    _apply_env_overrides()

    # Allow simple overrides via stdin: "threshold=0.7" "provider=claude" or blank
    stdin_input = sys.stdin.read().strip()
    test_mode = "--test" in sys.argv[1:] or os.environ.get("POWER_TOOLS_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    for token in stdin_input.split():
        if "=" in token:
            k, _, v = token.partition("=")
            k = k.strip().lower()
            if k == "threshold":
                try:
                    threshold = float(v)
                    if 0.0 <= threshold <= 1.0:
                        CONFIG["relevance_threshold"] = threshold
                        log(f"Threshold overridden → {CONFIG['relevance_threshold']}")
                    else:
                        log(f"Ignoring invalid threshold outside 0-1: {v}")
                except ValueError:
                    log(f"Ignoring invalid threshold: {v}")
            elif k == "provider":
                CONFIG["llm_provider"] = v.strip().lower()
                log(f"Provider overridden → {CONFIG['llm_provider']}")
            elif k == "test":
                test_mode = v.strip().lower() in {"1", "true", "yes", "on"}

    date_str = datetime.datetime.now(datetime.timezone.utc).strftime(CONFIG["date_format"])

    # 1. Fetch
    if test_mode:
        articles = sample_articles()
        updated_read_state = load_read_state()
        log("Test mode enabled; using sample RSS articles and leaving read state unchanged")
    else:
        articles, updated_read_state = fetch_feeds(CONFIG["lookback_hours"], read_state=load_read_state())
    if not articles:
        print("📰 No articles found in the last 24 hours.")
        return

    # 2. Score
    articles = score_articles(articles, test_mode=test_mode)

    # 3. Filter & sort
    relevant = [a for a in articles if a["score"] >= CONFIG["relevance_threshold"]]
    relevant.sort(key=lambda a: a["score"], reverse=True)
    relevant = relevant[: CONFIG["max_relevant_articles"]]
    log(f"Relevant articles after threshold: {len(relevant)}")

    # 4. BookStack
    page_url = None
    if relevant:
        md = build_bookstack_markdown(relevant, date_str)
        page_url = post_to_bookstack(md, date_str, test_mode=test_mode)
        if page_url:
            log(f"BookStack page: {page_url}")
        else:
            log("BookStack post failed.")
    if not test_mode:
        save_read_state(updated_read_state)

    # 5. Matrix message → stdout
    msg = build_matrix_message(relevant, page_url, date_str)
    print(msg)


if __name__ == "__main__":
    main()
