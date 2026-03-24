#!/usr/bin/env python3
"""Ingest journal RSS feeds into a local JSON snapshot."""

from __future__ import annotations

import datetime
from email.utils import parsedate_to_datetime
import re
import sys
from pathlib import Path

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.http_utils import fetch_bytes
from common.io_utils import CONFIGS_DIR, INGEST_DIR, STATE_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.runtime import is_test_mode


HTML_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    return WS_RE.sub(" ", HTML_RE.sub(" ", text or "")).strip()


def parse_date(entry) -> str:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, attr, None)
        if value:
            dt = datetime.datetime(*value[:6], tzinfo=datetime.timezone.utc)
            return dt.isoformat()
    for attr in ("published", "updated", "created"):
        raw = entry.get(attr)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(datetime.timezone.utc).isoformat()
            except Exception:
                pass
    return "unknown"


def article_key(link: str, title: str, published: str) -> str:
    return "||".join((link.strip(), title.strip().lower(), published.strip()))


def sample_items() -> list[dict]:
    return [
        {
            "source_type": "journal_rss",
            "feed": "Methods in Ecology and Evolution",
            "title": "Sample: Benchmarking LLM-assisted habitat classification workflows",
            "link": "https://example.org/sample-llm-habitat-classification",
            "summary": "Sample article for test mode showing an AI-enabled ecology methods paper.",
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tags": ["ai", "methods", "test-mode"],
        },
        {
            "source_type": "journal_rss",
            "feed": "Restoration Ecology",
            "title": "Sample: Community-led restoration outcomes across urban wetlands",
            "link": "https://example.org/sample-urban-wetlands-restoration",
            "summary": "Sample article for test mode covering restoration practice and social dimensions.",
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tags": ["restoration", "community", "test-mode"],
        },
    ]


def main() -> None:
    ensure_data_dirs()
    if is_test_mode():
        items = sample_items()
        dump_json(
            INGEST_DIR / "journal_articles.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": items,
                "test_mode": True,
            },
        )
        print(f"Wrote {len(items)} sample journal articles to {INGEST_DIR / 'journal_articles.json'}")
        return

    config = load_yaml(CONFIGS_DIR / "journals.yaml")
    lookback_hours = int(config.get("lookback_hours", 48))
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=lookback_hours)
    items: list[dict] = []
    existing_state = load_json(STATE_DIR / "rss_seen_articles.json", default={}) or {}
    seen_article_keys = set(existing_state.get("seen_article_keys", []))
    seen_links = set()

    failed_feeds: list[str] = []
    for feed in config.get("feeds", []):
        feed_name = feed.get("name", feed.get("url", "unknown"))
        try:
            payload = fetch_bytes(feed["url"])
        except Exception as exc:
            print(f"[rss_journals] WARNING: skipping {feed_name!r}: {exc}", file=sys.stderr)
            failed_feeds.append(feed_name)
            continue
        parsed = feedparser.parse(payload)
        for entry in parsed.entries[: int(feed.get("max_items", 50))]:
            published = parse_date(entry)
            if published != "unknown":
                dt = datetime.datetime.fromisoformat(published)
                if dt < cutoff:
                    continue

            link = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not link or link in seen_links:
                continue
            if not title:
                continue
            seen_links.add(link)
            key = article_key(link, title, published)
            if key in seen_article_keys:
                continue

            items.append(
                {
                    "source_type": "journal_rss",
                    "feed": feed_name,
                    "title": title,
                    "link": link,
                    "summary": strip_html(entry.get("summary", entry.get("description", ""))),
                    "published": published,
                    "tags": feed.get("tags", []),
                    "article_key": key,
                }
            )

    dump_json(
        INGEST_DIR / "journal_articles.json",
        {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": items},
    )
    dump_json(
        STATE_DIR / "rss_seen_articles.json",
        {
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "seen_article_keys": sorted(seen_article_keys),
            "pending_article_keys": sorted(item.get("article_key", "") for item in items if item.get("article_key")),
        },
    )
    print(f"Wrote {len(items)} journal articles to {INGEST_DIR / 'journal_articles.json'}")
    if failed_feeds:
        print(f"[rss_journals] {len(failed_feeds)} feed(s) unavailable: {', '.join(failed_feeds)}", file=sys.stderr)


if __name__ == "__main__":
    main()
