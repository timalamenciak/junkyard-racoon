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

    (ingest_dir / "news_items.json").write_text(
        json.dumps(
            {
                "relevant_items": [
                    {
                        "title": "Wetland restoration partnership announced",
                        "summary": "A new biodiversity restoration partnership was announced.",
                        "link": "https://example.org/news/wetland-restoration",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    for name, payload in {
        "job_openings.json": {"items": []},
        "collaborator_publications.json": {"items": []},
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
                "relevant_articles": [{"title": "Article item", "relevance_score": 0.9, "recommended_action": "Review", "link": "https://example.org/article"}],
                "relevant_grants": [{"title": "Grant item", "relevance_score": 0.8, "next_step": "Review", "link": "https://example.org/grant"}],
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
                        "posted_date": "Mar 24, 2026",
                        "application_deadline": "Apr 15, 2026",
                        "link": "https://example.org/jobs/restoration-ecologist",
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
    assert "Open Jobs" in index_html
    assert "Restoration Ecologist" in index_html
    assert "2026-03-26" in index_html
    assert "Priority Tasks" in index_html
    assert "Confirm the shortlist of papers for journal club." in index_html
    assert ".todo-list" in daily_html
    assert "Priority Tasks" in daily_html
    assert "Confirm the shortlist of papers for journal club." in daily_html
    assert (site_dir / "2026-03-26.html").exists()
