#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.pivot_email_parser import parse_pivot_email_opportunities
from ingest.grant_opportunities import parse_email_grant_items


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_parse_pivot_email_opportunities_extracts_multiple_items() -> None:
    message = {
        "subject": "Pivot-RP funding alert: wetland restoration OR conservation planning",
        "body_html": load_fixture("pivot_digest_email.html"),
        "body_text": "",
        "body_html_text": "",
    }

    items = parse_pivot_email_opportunities(message)

    assert len(items) == 2
    assert items[0]["title"] == "Urban Wetlands Adaptation Catalyst Grant"
    assert items[0]["sponsor"] == "Natural Sciences and Engineering Research Council of Canada"
    assert items[0]["deadline"] == "Apr 30, 2026"
    assert items[0]["link"] == "https://pivot.proquest.com/opportunity/111"
    assert "wetland restoration OR conservation planning" in items[0]["alert_context"]
    assert "Supports collaborative restoration pilots" in items[0]["summary"]

    assert items[1]["title"] == "Community Biodiversity Monitoring Partnership Fund"
    assert items[1]["sponsor"] == "Gordon and Betty Moore Foundation"
    assert items[1]["deadline"] == "May 15, 2026"
    assert items[1]["link"] == "https://pivot.proquest.com/opportunity/222"


def test_parse_email_grant_items_emits_partial_record_when_html_is_low_confidence() -> None:
    message = {
        "route_name": "pivot",
        "gmail_label": "pivot",
        "subject": "Pivot-RP alert: funding search digest",
        "summary": "Short alert summary",
        "body_text": "A funding alert is available. Review https://pivot.proquest.com/opportunity/333 for details.",
        "body_html": "",
        "body_html_text": "",
        "published": "2026-03-26T00:00:00+00:00",
        "tags": ["email", "pivot"],
        "message_id": "<partial@example.org>",
        "from": "Pivot-RP Alerts <alerts@example.org>",
    }

    items = parse_email_grant_items(message)

    assert len(items) == 1
    assert items[0]["title"] == "Pivot-RP alert: funding search digest"
    assert items[0]["link"] == "https://pivot.proquest.com/opportunity/333"
    assert items[0]["summary"] == "A funding alert is available. Review https://pivot.proquest.com/opportunity/333 for details."
    assert items[0]["parsing_confidence"] <= 0.45
    assert items[0]["message_id"] == "<partial@example.org>"


def test_parse_email_grant_items_preserves_core_output_schema_with_additive_fields() -> None:
    message = {
        "route_name": "pivot",
        "gmail_label": "pivot",
        "subject": "Pivot-RP alert: funding search digest",
        "summary": "Short alert summary",
        "body_text": "A funding alert is available. Review https://pivot.proquest.com/opportunity/333 for details.",
        "body_html": "",
        "body_html_text": "",
        "published": "2026-03-26T00:00:00+00:00",
        "tags": ["email", "pivot"],
        "message_id": "<partial@example.org>",
        "from": "Pivot-RP Alerts <alerts@example.org>",
    }

    item = parse_email_grant_items(message)[0]

    for key in ("source_type", "source", "title", "link", "summary", "published", "tags"):
        assert key in item
    for key in ("gmail_label", "message_id", "email_from", "deadline", "sponsor", "alert_context", "parsing_confidence"):
        assert key in item
