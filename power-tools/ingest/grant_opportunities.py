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
from common.email_source_registry import route_matches_target
from common.io_utils import CONFIGS_DIR, INGEST_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.email_utils import collapse_ws
from common.pivot_email_parser import parse_pivot_email_opportunities
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


def _normalize_email_grant_item(message: dict, parsed: dict) -> dict:
    return {
        "source_type": "grant_email",
        "source": message.get("route_name", message.get("mailbox", "Email")),
        "title": parsed.get("title", "").strip(),
        "link": (parsed.get("link") or "").strip(),
        "summary": (parsed.get("summary") or message.get("summary") or message.get("body_text") or "")[:1200],
        "published": message.get("published", "unknown"),
        "tags": list(message.get("tags", [])),
        "gmail_label": message.get("gmail_label", ""),
        "message_id": message.get("message_id", ""),
        "email_from": message.get("from", ""),
        "deadline": parsed.get("deadline", ""),
        "sponsor": parsed.get("sponsor", ""),
        "alert_context": parsed.get("alert_context", ""),
        "parsing_confidence": parsed.get("parsing_confidence", 0.0),
        "raw_email_subject": message.get("subject", ""),
    }


def parse_email_grant_items(message: dict) -> list[dict]:
    label = (message.get("gmail_label") or message.get("route_name") or "").strip().lower()
    if label in {"pivot", "grants"}:
        parsed_items = parse_pivot_email_opportunities(message)
        if parsed_items:
            return [_normalize_email_grant_item(message, parsed) for parsed in parsed_items]
    title = (message.get("subject") or "").strip()
    if not title:
        return []
    return [
        _normalize_email_grant_item(
            message,
            {
                "title": title,
                "link": (message.get("link") or "").strip(),
                "summary": (message.get("summary") or message.get("body_text") or "")[:1200],
                "deadline": "",
                "sponsor": "",
                "alert_context": collapse_ws(message.get("subject", "")) if message.get("subject") else "",
                "parsing_confidence": 0.35,
            },
        )
    ]


def load_manual_grant_items() -> list[dict]:
    """Load manually tracked grants from configs/manual_grants.yaml if present."""
    config_path = CONFIGS_DIR / "manual_grants.yaml"
    if not config_path.exists():
        return []
    config = load_yaml(config_path)
    today = datetime.date.today()
    items: list[dict] = []
    for entry in config.get("grants", []):
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title", "")).strip()
        if not title:
            continue
        # Skip entries whose deadline has already passed
        raw_deadline = str(entry.get("deadline", "")).strip()
        if raw_deadline:
            try:
                deadline_date = datetime.date.fromisoformat(raw_deadline[:10])
                if deadline_date < today:
                    continue
            except ValueError:
                pass
        items.append(
            {
                "source_type": "manual",
                "source": str(entry.get("funder", "Manual")).strip(),
                "title": title,
                "program": str(entry.get("program", "")).strip(),
                "link": str(entry.get("link", "")).strip(),
                "summary": str(entry.get("notes", "")).strip(),
                "amount": str(entry.get("amount", "")).strip(),
                "deadline": raw_deadline,
                "status": str(entry.get("status", "tracking")).strip().lower(),
                "published": today.isoformat(),
                "tags": ["manual", str(entry.get("funder", "")).lower().replace(" ", "-")],
                "always_surface": True,
            }
        )
    return items


def load_email_grant_items() -> list[dict]:
    payload = load_json(INGEST_DIR / "email_messages.json", default={"items": []}) or {}
    items: list[dict] = []
    seen_keys: set[str] = set()
    for message in payload.get("items", []):
        if not isinstance(message, dict) or not route_matches_target(message, "grant_opportunities"):
            continue
        for item in parse_email_grant_items(message):
            dedupe_key = "||".join(
                [
                    message.get("message_id", "").strip(),
                    item.get("link", "").strip(),
                    item.get("title", "").strip().lower(),
                ]
            )
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            items.append(item)
    return items


def main() -> None:
    ensure_data_dirs()
    if is_test_mode():
        rss_items = sample_items()
        email_items = load_email_grant_items()
        manual_items = load_manual_grant_items()
        items = rss_items + email_items + manual_items
        dump_json(
            INGEST_DIR / "grant_opportunities.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": items,
                "test_mode": True,
            },
        )
        print(f"Wrote {len(items)} sample grant opportunities to {INGEST_DIR / 'grant_opportunities.json'}")
        print(f"[grant_opportunities] counts: rss={len(rss_items)}, email={len(email_items)}, manual={len(manual_items)}, merged={len(items)}")
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

    rss_count = len(items)
    email_items = load_email_grant_items()
    manual_items = load_manual_grant_items()
    items.extend(email_items)
    items.extend(manual_items)
    dump_json(
        INGEST_DIR / "grant_opportunities.json",
        {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": items},
    )
    print(f"Wrote {len(items)} grant opportunities to {INGEST_DIR / 'grant_opportunities.json'}")
    print(f"[grant_opportunities] counts: rss={rss_count}, email={len(email_items)}, manual={len(manual_items)}, merged={len(items)}")
    if failed_sources:
        print(f"[grant_opportunities] {len(failed_sources)} source(s) unavailable: {', '.join(failed_sources)}", file=sys.stderr)


if __name__ == "__main__":
    main()
