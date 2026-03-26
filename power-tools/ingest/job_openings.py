#!/usr/bin/env python3
"""Ingest job openings from routed email newsletters."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.email_source_registry import route_matches_target
from common.io_utils import INGEST_DIR, dump_json, ensure_data_dirs, load_json
from common.job_email_parser import parse_job_email_items
from common.record_utils import canonicalize_url
from common.runtime import is_test_mode


def sample_items() -> list[dict]:
    return [
        {
            "source_type": "job_email",
            "source": "jobs",
            "title": "Restoration Ecologist",
            "organization": "Coastal Conservation Trust",
            "location": "Victoria, BC",
            "pay": "$72,000-$85,000 /year",
            "posted_date": "Mar 24, 2026",
            "application_deadline": "Apr 15, 2026",
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "category": "conservation",
            "link": "https://example.org/jobs/restoration-ecologist",
            "summary": "Applied restoration role supporting habitat recovery and biodiversity monitoring.",
            "tags": ["email", "jobs", "test-mode"],
            "gmail_label": "jobs",
            "message_id": "<sample-conservation-job@example.org>",
            "email_from": "Jobs Digest <jobs@example.org>",
        },
        {
            "source_type": "job_email",
            "source": "jobs",
            "title": "Postdoctoral Fellow in Biodiversity Restoration",
            "organization": "University of British Columbia",
            "location": "Vancouver, BC",
            "pay": "$68,000 /year",
            "posted_date": "Mar 22, 2026",
            "application_deadline": "Apr 30, 2026",
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "category": "academic",
            "link": "https://example.org/jobs/postdoc-biodiversity-restoration",
            "summary": "Postdoctoral position focused on restoration ecology and biodiversity outcomes.",
            "tags": ["email", "jobs", "test-mode"],
            "gmail_label": "jobs",
            "message_id": "<sample-academic-job@example.org>",
            "email_from": "Jobs Digest <jobs@example.org>",
        },
    ]


def _normalize_email_job_item(message: dict, parsed: dict) -> dict:
    return {
        "source_type": "job_email",
        "source": message.get("route_name", message.get("mailbox", "Email")),
        "title": parsed.get("title", "").strip(),
        "organization": parsed.get("organization", "").strip(),
        "location": parsed.get("location", "").strip(),
        "pay": parsed.get("pay", "").strip(),
        "posted_date": parsed.get("posted_date", "").strip(),
        "application_deadline": parsed.get("application_deadline", "").strip(),
        "published": message.get("published", "unknown"),
        "category": parsed.get("category", "").strip(),
        "link": canonicalize_url(parsed.get("link", "").strip()) or parsed.get("link", "").strip(),
        "summary": (parsed.get("summary") or message.get("summary") or message.get("body_text") or "")[:1200],
        "tags": list(message.get("tags", [])),
        "gmail_label": message.get("gmail_label", ""),
        "message_id": message.get("message_id", ""),
        "email_from": message.get("from", ""),
        "parsing_confidence": parsed.get("parsing_confidence", 0.0),
        "raw_email_subject": message.get("subject", ""),
    }


def load_email_job_items() -> list[dict]:
    payload = load_json(INGEST_DIR / "email_messages.json", default={"items": []}) or {}
    items: list[dict] = []
    seen_keys: set[str] = set()
    for message in payload.get("items", []):
        if not isinstance(message, dict) or not route_matches_target(message, "job_openings"):
            continue
        for parsed in parse_job_email_items(message):
            item = _normalize_email_job_item(message, parsed)
            dedupe_key = "||".join(
                [
                    item.get("title", "").strip().lower(),
                    item.get("organization", "").strip().lower(),
                    item.get("location", "").strip().lower(),
                    item.get("link", "").strip(),
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
        items = sample_items()
        dump_json(
            INGEST_DIR / "job_openings.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": items,
                "test_mode": True,
            },
        )
        print(f"Wrote {len(items)} sample job openings to {INGEST_DIR / 'job_openings.json'}")
        return

    items = load_email_job_items()
    dump_json(
        INGEST_DIR / "job_openings.json",
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": items,
        },
    )
    print(f"Wrote {len(items)} job openings to {INGEST_DIR / 'job_openings.json'}")


if __name__ == "__main__":
    main()
