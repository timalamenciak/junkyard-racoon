#!/usr/bin/env python3
"""Ingest grant opportunities from configured feeds."""

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
from common.runtime import is_test_mode


HTML_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def strip_html(text: str) -> str:
    return WS_RE.sub(" ", HTML_RE.sub(" ", text or "")).strip()


def parse_date(entry) -> str:
    raw = entry.get("published") or entry.get("updated") or ""
    if raw:
        try:
            return parsedate_to_datetime(raw).astimezone(datetime.timezone.utc).isoformat()
        except Exception:
            pass
    return "unknown"


def sample_items() -> list[dict]:
    return [
        {
            "source_type": "grant_rss",
            "source": "Sample Grants Feed",
            "title": "Sample: AI for biodiversity monitoring catalyst grant",
            "link": "https://example.org/sample-ai-biodiversity-grant",
            "summary": "Seed funding for applied AI, biodiversity monitoring, and community knowledge partnerships.",
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tags": ["ai", "biodiversity", "test-mode"],
        }
    ]


def main() -> None:
    ensure_data_dirs()
    if is_test_mode():
        items = sample_items()
        dump_json(
            INGEST_DIR / "grant_opportunities.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": items,
                "test_mode": True,
            },
        )
        print(f"Wrote {len(items)} sample grant opportunities to {INGEST_DIR / 'grant_opportunities.json'}")
        return

    config = load_yaml(CONFIGS_DIR / "grants.yaml")
    items: list[dict] = []
    seen_links = set()
    failed_sources: list[str] = []

    for source in config.get("sources", []):
        if source.get("type", "rss") != "rss":
            continue
        source_name = source.get("name", source["url"])
        try:
            payload = fetch_bytes(source["url"])
        except Exception as exc:
            print(f"[grant_opportunities] WARNING: skipping {source_name!r}: {exc}", file=sys.stderr)
            failed_sources.append(source_name)
            continue
        parsed = feedparser.parse(payload)
        for entry in parsed.entries[: int(source.get("max_items", 40))]:
            link = entry.get("link", "").strip()
            if not link or link in seen_links:
                continue
            seen_links.add(link)
            items.append(
                {
                    "source_type": "grant_rss",
                    "source": source_name,
                    "title": entry.get("title", "").strip(),
                    "link": link,
                    "summary": strip_html(entry.get("summary", entry.get("description", ""))),
                    "published": parse_date(entry),
                    "tags": source.get("tags", []),
                }
            )

    dump_json(
        INGEST_DIR / "grant_opportunities.json",
        {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": items},
    )
    print(f"Wrote {len(items)} grant opportunities to {INGEST_DIR / 'grant_opportunities.json'}")
    if failed_sources:
        print(f"[grant_opportunities] {len(failed_sources)} source(s) unavailable: {', '.join(failed_sources)}", file=sys.stderr)


if __name__ == "__main__":
    main()
