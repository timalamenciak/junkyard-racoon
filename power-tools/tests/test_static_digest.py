#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import output.publish_static_digest as publish_static_digest
import processing.daily_digest as daily_digest


def test_daily_digest_includes_news(monkeypatch, tmp_path) -> None:
    ingest_dir = tmp_path / "ingest"
    processing_dir = tmp_path / "processing"
    output_dir = tmp_path / "output"
    ingest_dir.mkdir(parents=True)
    processing_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    for name, payload in {
        "news_items.json": {"items": []},
        "job_openings.json": {"items": []},
        "collaborator_publications.json": {"items": []},
        "scored_news.json": {
            "relevant_items": [
                {
                    "title": "Wetland restoration partnership announced",
                    "summary": "A new biodiversity restoration partnership was announced.",
                    "link": "https://example.org/news/wetland-restoration",
                }
            ]
        },
        "scored_articles.json": {"relevant_items": []},
        "scored_grants.json": {"relevant_items": []},
        "obsidian_todos.json": {"items": []},
    }.items():
        target = processing_dir / name if name.startswith("scored_") or name.startswith("obsidian") else ingest_dir / name
        target.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(daily_digest, "INGEST_DIR", ingest_dir)
    monkeypatch.setattr(daily_digest, "PROCESSING_DIR", processing_dir)
    monkeypatch.setattr(daily_digest, "OUTPUT_DIR", output_dir)

    daily_digest.main()

    digest = json.loads((output_dir / "daily_digest.json").read_text(encoding="utf-8"))
    assert digest["relevant_news"][0]["title"] == "Wetland restoration partnership announced"


def test_daily_digest_routes_articles_into_mastodon_toots(monkeypatch, tmp_path) -> None:
    ingest_dir = tmp_path / "ingest"
    processing_dir = tmp_path / "processing"
    output_dir = tmp_path / "output"
    ingest_dir.mkdir(parents=True)
    processing_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    for name, payload in {
        "news_items.json": {"relevant_items": []},
        "job_openings.json": {"items": []},
        "collaborator_publications.json": {"items": []},
        "scored_grants.json": {"relevant_items": []},
        "scored_jobs.json": {"relevant_items": []},
        "obsidian_todos.json": {"items": []},
        "scored_articles.json": {
            "relevant_items": [
                {
                    "title": "Community-led restoration outcomes across urban wetlands",
                    "llm_summary": "A strong fit for the lab's restoration and social science focus.",
                    "relevance_score": 0.91,
                    "recommended_action": "Flag for journal club",
                    "link": "https://example.org/article/restoration-wetlands",
                }
            ]
        },
    }.items():
        target = processing_dir / name if name.startswith("scored_") or name.startswith("obsidian") else ingest_dir / name
        target.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(daily_digest, "INGEST_DIR", ingest_dir)
    monkeypatch.setattr(daily_digest, "PROCESSING_DIR", processing_dir)
    monkeypatch.setattr(daily_digest, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(
        daily_digest,
        "generate_mastodon_toots",
        lambda markdown, relevant_articles: [
            "General lab update.\nhttps://example.org/news/general",
            "Funding alert.\nhttps://example.org/grant/general",
            "Jobs update.",
            "Another lab update.",
            "Community note.",
        ],
    )

    daily_digest.main()

    digest = json.loads((output_dir / "daily_digest.json").read_text(encoding="utf-8"))
    assert len(digest["mastodon_toots"]) == 5
    assert "Community-led restoration outcomes across urban wetlands" in digest["mastodon_toots"][0]
    assert "https://example.org/article/restoration-wetlands" in digest["mastodon_toots"][0]


def test_daily_digest_does_not_fallback_to_raw_jobs_when_scored_relevant_items_are_empty(monkeypatch, tmp_path) -> None:
    ingest_dir = tmp_path / "ingest"
    processing_dir = tmp_path / "processing"
    output_dir = tmp_path / "output"
    ingest_dir.mkdir(parents=True)
    processing_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)

    for name, payload in {
        "news_items.json": {"relevant_items": []},
        "collaborator_publications.json": {"items": []},
        "job_openings.json": {
            "items": [
                {
                    "title": "Restoration Ecologist",
                    "summary": "Full newsletter body " * 40,
                    "link": "https://example.org/jobs/restoration-ecologist",
                }
            ]
        },
        "scored_grants.json": {"relevant_items": []},
        "scored_jobs.json": {"items": [{"title": "Restoration Ecologist"}], "relevant_items": []},
        "obsidian_todos.json": {"items": []},
        "scored_articles.json": {"relevant_items": []},
        "scored_news.json": {"relevant_items": []},
    }.items():
        target = processing_dir / name if name.startswith("scored_") or name.startswith("obsidian") else ingest_dir / name
        target.write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(daily_digest, "INGEST_DIR", ingest_dir)
    monkeypatch.setattr(daily_digest, "PROCESSING_DIR", processing_dir)
    monkeypatch.setattr(daily_digest, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(daily_digest, "is_test_mode", lambda: True)

    daily_digest.main()

    digest = json.loads((output_dir / "daily_digest.json").read_text(encoding="utf-8"))
    assert digest["open_jobs"] == []


def test_static_digest_publisher_builds_rolling_history_and_jobs_table(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    configs_dir = tmp_path / "configs"
    site_dir = tmp_path / "site"
    output_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    configs_dir.mkdir(parents=True)

    (configs_dir / "output.yaml").write_text(
        "\n".join(
            [
                "static_site:",
                "  public_url: https://lab.tim-a.ca/digest/",
                f"  site_dir: {site_dir.as_posix()}",
            ]
        ),
        encoding="utf-8",
    )
    (configs_dir / "lab_profile.yaml").write_text(
        "\n".join(
            [
                "job_relevance_threshold: 0.80",
            ]
        ),
        encoding="utf-8",
    )
    (output_dir / "daily_digest.json").write_text(
        json.dumps(
            {
                "date": "2026-03-26",
                "relevant_news": [{"title": "News item", "summary": "Summary", "link": "https://example.org/news"}],
                "relevant_articles": [
                    {
                        "title": "Article item",
                        "llm_summary": "A concise LLM summary for the article.",
                        "relevance_score": 0.9,
                        "recommended_action": "Review",
                        "link": "https://example.org/article",
                    }
                ],
                "relevant_grants": [
                    {
                        "title": "Grant item",
                        "llm_summary": "A concise LLM summary for the grant.",
                        "relevance_score": 0.8,
                        "next_step": "Review",
                        "link": "https://example.org/grant",
                    }
                ],
                "prioritized_todos": [
                    {
                        "task": "Confirm the shortlist of papers for journal club.",
                        "priority": "high",
                        "project": "Restoration Sprint",
                        "rationale": "Unblocks a near-term discussion.",
                    }
                ],
                "open_jobs": [
                    {
                        "title": "Restoration Ecologist",
                        "organization": "Coastal Conservation Trust",
                        "location": "Victoria, BC",
                        "pay": "$72,000-$85,000 /year",
                        "student_relevance_score": 0.91,
                        "student_fit_reason": "Strong field and restoration fit for lab trainees.",
                        "summary": "This long raw description should not be used in the jobs summary area.",
                        "posted_date": "Mar 24, 2026",
                        "application_deadline": "Apr 15, 2026",
                        "link": "https://example.org/jobs/restoration-ecologist",
                        "student_tags": ["restoration", "fieldwork"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(publish_static_digest, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(publish_static_digest, "STATE_DIR", state_dir)
    monkeypatch.setattr(publish_static_digest, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(publish_static_digest, "STATE_PATH", state_dir / "static_digest_site.json")

    publish_static_digest.main()

    index_html = (site_dir / "index.html").read_text(encoding="utf-8")
    daily_html = (site_dir / "2026-03-26.html").read_text(encoding="utf-8")
    assert "Jobs Board" in index_html
    assert "Restoration Ecologist" in index_html
    assert "2026-03-26" in index_html
    assert "A concise LLM summary for the article." in daily_html
    assert "A concise LLM summary for the grant." in daily_html
    assert "Strong field and restoration fit for lab trainees." in index_html
    assert "This long raw description should not be used in the jobs summary area." not in index_html
    assert "Confirm the shortlist of papers for journal club." not in daily_html
    assert (site_dir / "2026-03-26.html").exists()


def test_render_digest_section_includes_summaries_and_actions() -> None:
    html = publish_static_digest.render_digest_section(
        {
            "date": "2026-03-26",
            "relevant_news": [{"title": "News item", "summary": "News summary"}],
            "relevant_articles": [
                {
                    "title": "Article item",
                    "llm_summary": "Article summary",
                    "recommended_action": "Review for journal club",
                }
            ],
            "relevant_grants": [
                {
                    "title": "Grant item",
                    "llm_summary": "Grant summary",
                    "next_step": "Draft a note",
                }
            ],
            "prioritized_todos": [],
        }
    )

    assert "News summary" in html
    assert "Article summary" in html
    assert "Recommended action:" in html
    assert "Grant summary" in html
    assert "Next step:" in html


def test_render_today_briefing_keeps_repeated_grants_visible() -> None:
    html = publish_static_digest.render_today_briefing(
        [
            {
                "date": "2026-03-27",
                "relevant_news": [],
                "relevant_articles": [],
                "relevant_grants": [
                    {
                        "title": "Urban wetlands adaptation catalyst grant",
                        "llm_summary": "Still open and relevant for the lab.",
                        "link": "",
                    }
                ],
            },
            {
                "date": "2026-03-26",
                "relevant_news": [],
                "relevant_articles": [],
                "relevant_grants": [
                    {
                        "title": "Urban wetlands adaptation catalyst grant",
                        "llm_summary": "First seen yesterday.",
                        "link": "",
                    }
                ],
            },
        ],
        [],
    )

    assert "Urban wetlands adaptation catalyst grant" in html
    assert "Still open and relevant for the lab." in html
