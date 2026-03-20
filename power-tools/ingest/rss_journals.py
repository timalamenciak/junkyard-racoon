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
from common.io_utils import CONFIGS_DIR, INGEST_DIR, dump_json, ensure_data_dirs, load_yaml


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


def main() -> None:
    ensure_data_dirs()
    config = load_yaml(CONFIGS_DIR / "journals.yaml")
    lookback_hours = int(config.get("lookback_hours", 48))
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=lookback_hours)
    items: list[dict] = []
    seen_links = set()

    for feed in config.get("feeds", []):
        feed_name = feed.get("name", feed.get("url", "unknown"))
        payload = fetch_bytes(feed["url"])
        parsed = feedparser.parse(payload)
        for entry in parsed.entries[: int(feed.get("max_items", 50))]:
            published = parse_date(entry)
            if published != "unknown":
                dt = datetime.datetime.fromisoformat(published)
                if dt < cutoff:
                    continue

            link = entry.get("link", "").strip()
            if not link or link in seen_links:
                continue
            seen_links.add(link)

            items.append(
                {
                    "source_type": "journal_rss",
                    "feed": feed_name,
                    "title": entry.get("title", "").strip(),
                    "link": link,
                    "summary": strip_html(entry.get("summary", entry.get("description", ""))),
                    "published": published,
                    "tags": feed.get("tags", []),
                }
            )

    dump_json(
        INGEST_DIR / "journal_articles.json",
        {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": items},
    )
    print(f"Wrote {len(items)} journal articles to {INGEST_DIR / 'journal_articles.json'}")


if __name__ == "__main__":
    main()
