#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.news_email_parser import parse_news_email_items
from ingest.research_news import load_email_news_items, score_news_item
from common.io_utils import INGEST_DIR, dump_json


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_parse_news_email_items_extracts_multiple_headlines() -> None:
    message = {
        "subject": "Research news digest",
        "body_html": load_fixture("news_digest_email.html"),
        "body_text": "",
        "body_html_text": "",
    }

    items = parse_news_email_items(message)

    assert len(items) == 2
    assert items[0]["title"] == "Canadian restoration policy expands wetland conservation funding"
    assert items[0]["published_hint"] == "March 20, 2026"
    assert "Canadian environmental policy" in items[0]["summary"]


def test_score_news_item_matches_tunable_keywords() -> None:
    score, matched = score_news_item(
        "Canadian restoration policy expands wetland conservation funding",
        "This story covers biodiversity and environmental assessment.",
        ["restoration", "biodiversity", "environmental assessment", "ontology"],
    )

    assert score == 3
    assert matched == ["restoration", "biodiversity", "environmental assessment"]


def test_load_email_news_items_filters_by_keywords() -> None:
    dump_json(
        INGEST_DIR / "email_messages.json",
        {
            "generated_at": "2026-03-26T00:00:00+00:00",
            "items": [
                {
                    "target": "news_items",
                    "route_name": "news",
                    "mailbox": "news",
                    "gmail_label": "news",
                    "message_id": "<news@example.org>",
                    "subject": "Research news digest",
                    "from": "Research News Alerts <alerts@example.org>",
                    "published": "2026-03-26T00:00:00+00:00",
                    "summary": "Digest",
                    "body_text": "",
                    "body_html": load_fixture("news_digest_email.html"),
                    "body_html_text": "",
                    "tags": ["email", "news"],
                }
            ],
        },
    )

    items = load_email_news_items(
        keywords=["ecology", "restoration", "conservation", "biodiversity", "canadian environmental policy"],
        minimum_keyword_score=1,
    )

    assert len(items) == 1
    assert items[0]["title"] == "Canadian restoration policy expands wetland conservation funding"
    assert items[0]["keyword_score"] >= 2
    assert "restoration" in items[0]["matched_keywords"]
    assert items[0]["gmail_label"] == "news"
