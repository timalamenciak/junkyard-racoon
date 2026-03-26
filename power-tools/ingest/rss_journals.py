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
from common.email_source_registry import route_matches_target
from common.io_utils import CONFIGS_DIR, INGEST_DIR, STATE_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.journal_email_parser import parse_journal_email_articles
from common.record_utils import canonicalize_url, fingerprint_record, merge_records
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


def merge_journal_items(items: list[dict]) -> list[dict]:
    merged_by_fingerprint: dict[str, dict] = {}
    order: list[str] = []
    for item in items:
        canonical_link = canonicalize_url(item.get("link", ""))
        if canonical_link:
            item["link"] = canonical_link
        item.setdefault("provenance", [])
        item.setdefault("sources", [])
        if item.get("source_type") == "journal_rss":
            item["provenance"] = ["rss"]
            item["sources"] = [item.get("feed", "")]
        elif item.get("source_type") == "journal_email":
            item["provenance"] = ["email"]
            item["sources"] = [item.get("gmail_label", "") or item.get("feed", "")]

        fingerprint = fingerprint_record(item.get("title", ""), item.get("link", ""), item.get("published", ""))
        if not fingerprint.strip("|"):
            fingerprint = item.get("article_key", "")
        if fingerprint not in merged_by_fingerprint:
            merged_by_fingerprint[fingerprint] = item
            order.append(fingerprint)
            continue
        merged_by_fingerprint[fingerprint] = merge_records(
            merged_by_fingerprint[fingerprint],
            item,
            preserve_keys=("article_key",),
        )
    return [merged_by_fingerprint[key] for key in order]


def load_email_article_items(seen_article_keys: set[str], seen_links: set[str] | None = None) -> list[dict]:
    payload = load_json(INGEST_DIR / "email_messages.json", default={"items": []}) or {}
    items: list[dict] = []
    email_seen_fingerprints: set[str] = set()
    for message in payload.get("items", []):
        if not isinstance(message, dict):
            continue
        if not route_matches_target(message, "journal_articles"):
            continue
        parsed_items = parse_journal_email_articles(message)
        for parsed in parsed_items:
            title = (parsed.get("title") or "").strip()
            if not title:
                continue
            published = message.get("published", "unknown")
            link = (parsed.get("link") or "").strip()
            key = article_key(link or f"message:{message.get('message_id', '')}", title, published)
            if key in seen_article_keys:
                continue
            fingerprint = fingerprint_record(title, link, published)
            if fingerprint in email_seen_fingerprints:
                continue
            email_seen_fingerprints.add(fingerprint)
            items.append(
                {
                    "source_type": "journal_email",
                    "feed": parsed.get("journal_name") or message.get("route_name", message.get("mailbox", "Email")),
                    "title": title,
                    "link": link,
                    "summary": (parsed.get("summary") or message.get("summary") or message.get("body_text") or "")[:1200],
                    "published": published,
                    "tags": list(message.get("tags", [])),
                    "article_key": key,
                    "gmail_label": message.get("gmail_label", ""),
                    "message_id": message.get("message_id", ""),
                    "email_from": message.get("from", ""),
                    "authors": parsed.get("authors", ""),
                    "doi": parsed.get("doi", ""),
                    "published_hint": parsed.get("published_hint", ""),
                    "parsing_confidence": parsed.get("parsing_confidence", 0.0),
                    "canonical_link": canonicalize_url(link),
                }
            )
    return items


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
    existing_state = load_json(STATE_DIR / "rss_seen_articles.json", default={}) or {}
    seen_article_keys = set(existing_state.get("seen_article_keys", []))
    if is_test_mode():
        rss_items = sample_items()
        email_items = load_email_article_items(seen_article_keys)
        items = merge_journal_items(rss_items + email_items)
        dump_json(
            INGEST_DIR / "journal_articles.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": items,
                "test_mode": True,
            },
        )
        print(f"Wrote {len(items)} sample journal articles to {INGEST_DIR / 'journal_articles.json'}")
        print(f"[rss_journals] counts: rss={len(rss_items)}, email={len(email_items)}, merged={len(items)}")
        return

    config = load_yaml(CONFIGS_DIR / "journals.yaml")
    lookback_hours = int(config.get("lookback_hours", 48))
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=lookback_hours)
    items: list[dict] = []
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
                    "canonical_link": canonicalize_url(link),
                }
            )

    rss_count = len(items)
    email_items = load_email_article_items(seen_article_keys, seen_links)
    items.extend(email_items)
    items = merge_journal_items(items)
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
    print(f"[rss_journals] counts: rss={rss_count}, email={len(email_items)}, merged={len(items)}")
    if failed_feeds:
        print(f"[rss_journals] {len(failed_feeds)} feed(s) unavailable: {', '.join(failed_feeds)}", file=sys.stderr)


if __name__ == "__main__":
    main()
