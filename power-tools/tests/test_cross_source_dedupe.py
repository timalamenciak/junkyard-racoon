#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ingest.research_news import merge_news_items
from ingest.rss_journals import article_identity, merge_journal_items


def test_merge_journal_items_dedupes_across_rss_and_email_with_provenance() -> None:
    rss_item = {
        "source_type": "journal_rss",
        "feed": "Restoration Ecology",
        "title": "Community-led restoration outcomes across urban wetlands",
        "link": "https://onlinelibrary.wiley.com/doi/10.1002/rec.12345?utm_source=rss",
        "summary": "Short RSS summary.",
        "published": "2026-03-26T00:00:00+00:00",
        "tags": ["restoration"],
        "article_key": "rss-key",
    }
    email_item = {
        "source_type": "journal_email",
        "feed": "Restoration Ecology",
        "title": "Community-led restoration outcomes across urban wetlands",
        "link": "https://onlinelibrary.wiley.com/doi/10.1002/rec.12345",
        "summary": "Longer email summary with more context.",
        "published": "2026-03-26T09:00:00+00:00",
        "tags": ["email", "journals"],
        "article_key": "email-key",
        "gmail_label": "journals",
        "authors": "Alex Rivera, Sam Lee",
        "doi": "10.1002/rec.12345",
    }

    items = merge_journal_items([rss_item, email_item])

    assert len(items) == 1
    assert items[0]["provenance"] == ["rss", "email"]
    assert items[0]["sources"] == ["Restoration Ecology", "journals"]
    assert items[0]["link"] == "https://onlinelibrary.wiley.com/doi/10.1002/rec.12345"
    assert items[0]["summary"] == "Short RSS summary."
    assert items[0]["authors"] == "Alex Rivera, Sam Lee"
    assert items[0]["doi"] == "10.1002/rec.12345"
    assert items[0]["article_key"] == "rss-key"


def test_merge_journal_items_dedupes_openalex_against_rss_by_doi() -> None:
    rss_item = {
        "source_type": "journal_rss",
        "feed": "Restoration Ecology",
        "title": "Community-led restoration outcomes across urban wetlands",
        "link": "https://onlinelibrary.wiley.com/doi/10.1002/rec.12345?utm_source=rss",
        "summary": "Short RSS summary.",
        "published": "2026-03-26T00:00:00+00:00",
        "tags": ["restoration"],
        "article_key": "rss-key",
    }
    openalex_item = {
        "source_type": "journal_openalex",
        "feed": "Restoration Ecology",
        "title": "Community-led restoration outcomes across urban wetlands",
        "link": "https://doi.org/10.1002/rec.12345",
        "summary": "Reconstructed abstract from OpenAlex.",
        "published": "2026-03-26T00:00:00+00:00",
        "tags": ["openalex", "restoration ecology"],
        "article_key": "openalex-key",
        "doi": "https://doi.org/10.1002/rec.12345",
        "openalex_id": "https://openalex.org/W123",
        "authors": "Alex Rivera, Sam Lee",
        "open_access": True,
    }

    items = merge_journal_items([rss_item, openalex_item])

    assert len(items) == 1
    assert items[0]["provenance"] == ["rss", "openalex"]
    assert items[0]["sources"] == ["Restoration Ecology"]
    assert items[0]["summary"] == "Short RSS summary."
    assert items[0]["authors"] == "Alex Rivera, Sam Lee"
    assert items[0]["doi"] == "10.1002/rec.12345"
    assert items[0]["openalex_id"] == "https://openalex.org/W123"


def test_article_identity_prefers_doi_extracted_from_link() -> None:
    item = {
        "title": "Community-led restoration outcomes across urban wetlands",
        "link": "https://onlinelibrary.wiley.com/doi/10.1002/rec.12345?utm_source=rss",
        "published": "2026-03-26T00:00:00+00:00",
    }

    assert article_identity(item) == "doi::10.1002/rec.12345"


def test_merge_news_items_dedupes_canonical_urls_and_preserves_richer_metadata() -> None:
    rss_item = {
        "source_type": "news_rss",
        "source": "Mongabay Conservation News",
        "title": "Canadian restoration policy expands wetland conservation funding",
        "link": "https://example.org/news/restoration-policy-canada?utm_source=rss",
        "summary": "RSS summary.",
        "published": "2026-03-20T00:00:00+00:00",
        "tags": ["news", "conservation"],
        "keyword_score": 3,
        "matched_keywords": ["restoration", "conservation", "canada"],
        "is_relevant": True,
    }
    email_item = {
        "source_type": "news_email",
        "source": "news",
        "title": "Canadian restoration policy expands wetland conservation funding",
        "link": "https://example.org/news/restoration-policy-canada",
        "summary": "",
        "published": "2026-03-20T08:00:00+00:00",
        "tags": ["email", "news"],
        "gmail_label": "news",
        "message_id": "<news@example.org>",
        "email_from": "Research News Alerts <alerts@example.org>",
        "published_hint": "March 20, 2026",
        "parsing_confidence": 0.75,
        "keyword_score": 2,
        "matched_keywords": ["restoration", "conservation"],
        "is_relevant": True,
    }

    items = merge_news_items([rss_item, email_item])

    assert len(items) == 1
    assert items[0]["provenance"] == ["rss", "email"]
    assert items[0]["sources"] == ["Mongabay Conservation News", "news"]
    assert items[0]["link"] == "https://example.org/news/restoration-policy-canada"
    assert items[0]["summary"] == "RSS summary."
    assert items[0]["published_hint"] == "March 20, 2026"
    assert items[0]["message_id"] == "<news@example.org>"
