#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import processing.score_articles as score_articles


def test_score_articles_llm_skips_malformed_items(monkeypatch) -> None:
    articles = [
        {
            "title": "Community-led restoration outcomes across urban wetlands",
            "feed": "Restoration Ecology",
            "summary": "A strong restoration paper.",
            "tags": ["restoration"],
        }
    ]
    lab_profile = {
        "research_interests": ["Restoration ecology"],
        "article_scoring_rules": ["Prefer directly useful papers."],
    }

    monkeypatch.setattr(score_articles, "chat_completion", lambda *args, **kwargs: "ignored")
    monkeypatch.setattr(
        score_articles,
        "extract_json_payload",
        lambda raw: [
            "unexpected string item",
            {
                "index": 0,
                "score": 0.92,
                "summary": "Useful for the lab.",
                "rationale": "Strong methods and topical fit.",
                "recommended_action": "Flag for journal club.",
            },
        ],
    )

    score_articles.score_articles_llm(articles, lab_profile)

    assert articles[0]["relevance_score"] == 0.92
    assert articles[0]["llm_summary"] == "Useful for the lab."
    assert articles[0]["recommended_action"] == "Flag for journal club."
