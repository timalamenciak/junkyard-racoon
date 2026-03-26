#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.journal_email_parser import parse_journal_email_articles
from ingest.rss_journals import load_email_article_items
from common.io_utils import INGEST_DIR, dump_json


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_parse_journal_email_articles_extracts_multiple_links() -> None:
    message = {
        "subject": "Latest issue alert: Restoration Ecology",
        "body_html": load_fixture("journal_toc_email.html"),
        "body_text": "",
        "body_html_text": "",
    }

    items = parse_journal_email_articles(message)

    assert len(items) == 2
    assert items[0]["title"] == "Community-led restoration outcomes across urban wetlands"
    assert items[0]["journal_name"] == "Restoration Ecology"
    assert items[0]["authors"] == "Alex Rivera, Sam Lee"
    assert items[0]["published_hint"] == "March 12, 2026"
    assert items[0]["doi"] == "10.1002/rec.12345"
    assert "participatory wetland restoration outcomes" in items[0]["summary"]

    assert items[1]["title"] == "Monitoring co-benefits in climate-adapted marsh restoration"
    assert items[1]["authors"] == "Priya Natarajan, Elise Wong"
    assert items[1]["published_hint"] == "March 14, 2026"
    assert items[1]["doi"] == "10.1002/rec.67890"


def test_load_email_article_items_preserves_existing_schema_and_additive_fields() -> None:
    dump_json(
        INGEST_DIR / "email_messages.json",
        {
            "generated_at": "2026-03-26T00:00:00+00:00",
            "items": [
                {
                    "target": "journal_articles",
                    "route_name": "journals",
                    "mailbox": "journals",
                    "gmail_label": "journals",
                    "message_id": "<journal@example.org>",
                    "subject": "Latest issue alert: Restoration Ecology",
                    "from": "Wiley Alerts <alerts@example.org>",
                    "published": "2026-03-26T00:00:00+00:00",
                    "summary": "Issue digest",
                    "body_text": "",
                    "body_html": load_fixture("journal_toc_email.html"),
                    "body_html_text": "",
                    "tags": ["email", "journals"],
                }
            ],
        },
    )

    items = load_email_article_items(seen_article_keys=set(), seen_links=set())

    assert len(items) == 2
    first = items[0]
    assert first["source_type"] == "journal_email"
    assert first["feed"] == "Restoration Ecology"
    assert first["title"] == "Community-led restoration outcomes across urban wetlands"
    assert first["link"] == "https://onlinelibrary.wiley.com/doi/10.1002/rec.12345"
    assert first["gmail_label"] == "journals"
    assert first["message_id"] == "<journal@example.org>"
    assert first["email_from"] == "Wiley Alerts <alerts@example.org>"
    assert first["authors"] == "Alex Rivera, Sam Lee"
    assert first["doi"] == "10.1002/rec.12345"
    assert first["published_hint"] == "March 12, 2026"
    assert "article_key" in first
