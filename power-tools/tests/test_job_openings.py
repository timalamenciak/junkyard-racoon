#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.job_email_parser import parse_job_email_items
from common.io_utils import INGEST_DIR, dump_json
import ingest.job_openings as job_openings
import output.publish_hedgedoc as publish_hedgedoc


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_parse_job_email_items_extracts_conservation_and_academic_roles() -> None:
    message = {
        "subject": "Jobs digest",
        "body_html": load_fixture("jobs_digest_email.html"),
        "body_text": "",
        "body_html_text": "",
    }

    items = parse_job_email_items(message)

    assert len(items) == 2
    assert items[0]["title"] == "Restoration Ecologist"
    assert items[0]["organization"] == "Coastal Conservation Trust"
    assert items[0]["location"] == "Victoria, BC"
    assert items[0]["pay"] == "$72,000-$85,000 /year"
    assert items[0]["application_deadline"] == "Apr 15, 2026"
    assert items[0]["category"] == "conservation"

    assert items[1]["title"] == "Postdoctoral Fellow in Biodiversity Restoration"
    assert items[1]["organization"] == "University of British Columbia"
    assert items[1]["category"] == "academic"


def test_load_email_job_items_reads_jobs_target() -> None:
    dump_json(
        INGEST_DIR / "email_messages.json",
        {
            "generated_at": "2026-03-26T00:00:00+00:00",
            "items": [
                {
                    "target": "job_openings",
                    "route_name": "jobs",
                    "mailbox": "jobs",
                    "gmail_label": "jobs",
                    "message_id": "<jobs@example.org>",
                    "subject": "Jobs digest",
                    "from": "Jobs Digest <alerts@example.org>",
                    "published": "2026-03-26T00:00:00+00:00",
                    "summary": "Digest",
                    "body_text": "",
                    "body_html": load_fixture("jobs_digest_email.html"),
                    "body_html_text": "",
                    "tags": ["email", "jobs"],
                }
            ],
        },
    )

    items = job_openings.load_email_job_items()

    assert len(items) == 2
    assert {item["category"] for item in items} == {"academic", "conservation"}
    assert all(item["gmail_label"] == "jobs" for item in items)


def test_publish_hedgedoc_tracks_jobs_records_in_test_mode(monkeypatch, tmp_path) -> None:
    output_dir = tmp_path / "output"
    state_dir = tmp_path / "state"
    configs_dir = tmp_path / "configs"
    output_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    configs_dir.mkdir(parents=True)

    (output_dir / "daily_digest.json").write_text(
        json.dumps(
            {
                "date": "2026-03-26",
                "relevant_articles": [],
                "relevant_grants": [],
                "prioritized_todos": [],
                "collaborator_publications": [],
                "open_jobs": [
                    {
                        "title": "Restoration Ecologist",
                        "organization": "Coastal Conservation Trust",
                        "location": "Victoria, BC",
                        "pay": "$72,000-$85,000 /year",
                        "posted_date": "Mar 24, 2026",
                        "application_deadline": "Apr 15, 2026",
                        "category": "conservation",
                        "link": "https://example.org/jobs/restoration-ecologist",
                    },
                    {
                        "title": "Postdoctoral Fellow in Biodiversity Restoration",
                        "organization": "University of British Columbia",
                        "location": "Vancouver, BC",
                        "pay": "$68,000 /year",
                        "posted_date": "Mar 22, 2026",
                        "application_deadline": "Apr 30, 2026",
                        "category": "academic",
                        "link": "https://example.org/jobs/postdoc-biodiversity-restoration",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (configs_dir / "output.yaml").write_text(
        "\n".join(
            [
                "hedgedoc:",
                "  url: https://hedgedoc.example.org",
                "  notes:",
                "    articles: articles-note",
                "    grants: grants-note",
                "    tasks: tasks-note",
                "    jobs: jobs-note",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(publish_hedgedoc, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(publish_hedgedoc, "STATE_DIR", state_dir)
    monkeypatch.setattr(publish_hedgedoc, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(publish_hedgedoc, "ROLLUP_STATE_PATH", state_dir / "hedgedoc_rollups.json")
    monkeypatch.setattr(publish_hedgedoc, "is_test_mode", lambda: True)

    publish_hedgedoc.main()

    state = json.loads((state_dir / "hedgedoc_rollups.json").read_text(encoding="utf-8"))
    jobs_state = state["sections"]["jobs"]
    assert len(jobs_state["records"]) == 2

    publish = json.loads((output_dir / "hedgedoc_publish.json").read_text(encoding="utf-8"))
    assert publish["jobs_url"] == "https://hedgedoc.example.org/jobs-note-rolling-2026-03-26"


def test_publish_note_falls_back_to_random_note(monkeypatch) -> None:
    monkeypatch.setattr(
        publish_hedgedoc,
        "_publish_note_to_alias",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(publish_hedgedoc.HedgeDocPublishError("alias failed")),
    )
    monkeypatch.setattr(
        publish_hedgedoc,
        "_publish_note_random",
        lambda *_args, **_kwargs: "https://hedgedoc.example.org/random-note",
    )

    url, mode = publish_hedgedoc.publish_note(
        "https://hedgedoc.example.org",
        "articles-note",
        "# test",
        {"Cookie": "hedgedoc.sid=abc", "Content-Type": "text/markdown; charset=utf-8"},
        allow_random_fallback=True,
    )

    assert url == "https://hedgedoc.example.org/random-note"
    assert mode.startswith("random_fallback:")


def test_ensure_session_authenticated_requires_logged_in_user(monkeypatch) -> None:
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"name":"Tim"}'

    monkeypatch.setattr(publish_hedgedoc, "_request", lambda *_args, **_kwargs: FakeResponse())

    payload = publish_hedgedoc.ensure_session_authenticated(
        "https://hedgedoc.example.org",
        {"Cookie": "hedgedoc.sid=abc", "Content-Type": "application/json"},
    )

    assert payload["name"] == "Tim"
