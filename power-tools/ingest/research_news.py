#!/usr/bin/env python3
"""Ingest research-relevant news from RSS feeds and routed email newsletters."""

from __future__ import annotations

import datetime
from email.utils import parsedate_to_datetime
import re
import sys
from pathlib import Path

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.http_utils import fetch_bytes
from common.email_source_registry import route_matches_target
from common.io_utils import CONFIGS_DIR, INGEST_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.news_email_parser import parse_news_email_items
from common.record_utils import canonicalize_url, fingerprint_record, merge_records
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


def score_news_item(title: str, summary: str, keywords: list[str]) -> tuple[int, list[str]]:
    haystack = f"{title} {summary}".lower()
    matched = [keyword for keyword in keywords if keyword.lower() in haystack]
    return len(matched), matched


def merge_news_items(items: list[dict]) -> list[dict]:
    merged_by_fingerprint: dict[str, dict] = {}
    order: list[str] = []
    for item in items:
        canonical_link = canonicalize_url(item.get("link", ""))
        if canonical_link:
            item["link"] = canonical_link
        item.setdefault("provenance", [])
        item.setdefault("sources", [])
        if item.get("source_type") == "news_rss":
            item["provenance"] = ["rss"]
            item["sources"] = [item.get("source", "")]
        elif item.get("source_type") == "news_email":
            item["provenance"] = ["email"]
            item["sources"] = [item.get("gmail_label", "") or item.get("source", "")]

        fingerprint = fingerprint_record(item.get("title", ""), item.get("link", ""), item.get("published", ""))
        if not fingerprint.strip("|"):
            fingerprint = "||".join((item.get("title", "").lower(), item.get("message_id", ""), item.get("source", "")))
        if fingerprint not in merged_by_fingerprint:
            merged_by_fingerprint[fingerprint] = item
            order.append(fingerprint)
            continue
        merged_by_fingerprint[fingerprint] = merge_records(merged_by_fingerprint[fingerprint], item)
    return [merged_by_fingerprint[key] for key in order]


def sample_items() -> list[dict]:
    keywords = ["restoration", "conservation", "canada"]
    sample = {
        "source_type": "news_email",
        "source": "news",
        "title": "Research news: Indigenous-led wetland restoration agreement announced",
        "link": "https://example.org/news/indigenous-led-wetland-restoration",
        "summary": "News alert covering a restoration partnership and policy-relevant conservation funding signals in Canada.",
        "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "tags": ["email", "news", "test-mode"],
        "gmail_label": "news",
        "message_id": "<sample-news@example.org>",
        "email_from": "Research News Alerts <alerts@example.org>",
    }
    score, matched = score_news_item(sample["title"], sample["summary"], keywords)
    sample["keyword_score"] = score
    sample["matched_keywords"] = matched
    sample["is_relevant"] = True
    return [sample]


def load_email_news_items(keywords: list[str], minimum_keyword_score: int) -> list[dict]:
    payload = load_json(INGEST_DIR / "email_messages.json", default={"items": []}) or {}
    items: list[dict] = []
    seen_keys: set[str] = set()
    for message in payload.get("items", []):
        if not isinstance(message, dict) or not route_matches_target(message, "news_items"):
            continue
        for parsed in parse_news_email_items(message):
            title = (parsed.get("title") or "").strip()
            if not title:
                continue
            dedupe_key = fingerprint_record(title, parsed.get("link", "").strip(), message.get("published", "unknown"))
            if not dedupe_key.strip("|"):
                dedupe_key = "||".join([message.get("message_id", "").strip(), parsed.get("link", "").strip(), title.lower()])
            if dedupe_key in seen_keys:
                continue
            score, matched = score_news_item(title, parsed.get("summary", ""), keywords)
            if score < minimum_keyword_score:
                continue
            seen_keys.add(dedupe_key)
            items.append(
                {
                    "source_type": "news_email",
                    "source": message.get("route_name", message.get("mailbox", "Email")),
                    "title": title,
                    "link": (parsed.get("link") or "").strip(),
                    "summary": (parsed.get("summary") or message.get("summary") or message.get("body_text") or "")[:1200],
                    "published": message.get("published", "unknown"),
                    "tags": list(message.get("tags", [])),
                    "gmail_label": message.get("gmail_label", ""),
                    "message_id": message.get("message_id", ""),
                    "email_from": message.get("from", ""),
                    "published_hint": parsed.get("published_hint", ""),
                    "parsing_confidence": parsed.get("parsing_confidence", 0.0),
                    "keyword_score": score,
                    "matched_keywords": matched,
                    "is_relevant": True,
                    "canonical_link": canonicalize_url(parsed.get("link", "").strip()),
                }
            )
    return items


def load_rss_news_items(config: dict, keywords: list[str], minimum_keyword_score: int) -> tuple[list[dict], list[str]]:
    items: list[dict] = []
    seen_fingerprints: set[str] = set()
    failed_feeds: list[str] = []
    max_items_per_feed = int(config.get("max_items_per_feed", 40))

    for feed in config.get("feeds", []):
        feed_name = feed.get("name", feed.get("url", "unknown"))
        try:
            payload = fetch_bytes(feed["url"])
        except Exception as exc:
            print(f"[research_news] WARNING: skipping {feed_name!r}: {exc}", file=sys.stderr)
            failed_feeds.append(feed_name)
            continue
        parsed = feedparser.parse(payload)
        for entry in parsed.entries[:max_items_per_feed]:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            summary = strip_html(entry.get("summary", entry.get("description", "")))
            score, matched = score_news_item(title, summary, keywords)
            if score < minimum_keyword_score:
                continue
            fingerprint = fingerprint_record(title, link, parse_date(entry))
            if fingerprint in seen_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            items.append(
                {
                    "source_type": "news_rss",
                    "source": feed_name,
                    "title": title,
                    "link": link,
                    "summary": summary[:1200],
                    "published": parse_date(entry),
                    "tags": list(feed.get("tags", [])),
                    "keyword_score": score,
                    "matched_keywords": matched,
                    "is_relevant": True,
                    "canonical_link": canonicalize_url(link),
                }
            )
    return items, failed_feeds


def main() -> None:
    ensure_data_dirs()
    config = load_yaml(CONFIGS_DIR / "news.yaml")
    keywords = [str(value) for value in config.get("keywords", []) if str(value).strip()]
    minimum_keyword_score = int(config.get("minimum_keyword_score", 1))

    if is_test_mode():
        rss_items: list[dict] = []
        email_items = sample_items()
        items = merge_news_items(email_items)
        dump_json(
            INGEST_DIR / "news_items.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": items,
                "relevant_items": items,
                "test_mode": True,
            },
        )
        print(f"Wrote {len(items)} sample news items to {INGEST_DIR / 'news_items.json'}")
        print(f"[research_news] counts: rss={len(rss_items)}, email={len(email_items)}, merged={len(items)}")
        return

    rss_items, failed_feeds = load_rss_news_items(config, keywords, minimum_keyword_score)
    email_items = load_email_news_items(keywords, minimum_keyword_score)
    merged_items = merge_news_items(rss_items + email_items)

    dump_json(
        INGEST_DIR / "news_items.json",
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": merged_items,
            "relevant_items": merged_items,
            "filters": {
                "keywords": keywords,
                "minimum_keyword_score": minimum_keyword_score,
            },
        },
    )
    print(f"Wrote {len(merged_items)} news items to {INGEST_DIR / 'news_items.json'}")
    print(f"[research_news] counts: rss={len(rss_items)}, email={len(email_items)}, merged={len(merged_items)}")
    if failed_feeds:
        print(f"[research_news] {len(failed_feeds)} feed(s) unavailable: {', '.join(failed_feeds)}", file=sys.stderr)


if __name__ == "__main__":
    main()
