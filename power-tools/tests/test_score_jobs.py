#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import processing.score_jobs as score_jobs


def test_score_jobs_test_mode_generates_student_fields(monkeypatch, tmp_path) -> None:
    ingest_dir = tmp_path / "ingest"
    processing_dir = tmp_path / "processing"
    configs_dir = tmp_path / "configs"
    ingest_dir.mkdir(parents=True)
    processing_dir.mkdir(parents=True)
    configs_dir.mkdir(parents=True)

    (configs_dir / "lab_profile.yaml").write_text(
        "\n".join(
            [
                "research_interests:",
                "  - restoration ecology",
                "job_relevance_threshold: 0.45",
            ]
        ),
        encoding="utf-8",
    )
    (ingest_dir / "job_openings.json").write_text(
        json.dumps(
            {
                "items": [
                    {
                        "title": "Field Technician",
                        "organization": "Coastal Conservation Trust",
                        "location": "Remote, Canada",
                        "pay": "$24/hour",
                        "summary": "Fieldwork-heavy conservation role suitable for students.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(score_jobs, "INGEST_DIR", ingest_dir)
    monkeypatch.setattr(score_jobs, "PROCESSING_DIR", processing_dir)
    monkeypatch.setattr(score_jobs, "CONFIGS_DIR", configs_dir)
    monkeypatch.setattr(score_jobs, "is_test_mode", lambda: True)

    score_jobs.main()

    payload = json.loads((processing_dir / "scored_jobs.json").read_text(encoding="utf-8"))
    item = payload["items"][0]
    assert item["pay_normalized"] == "$24/hour"
    assert item["student_relevance_score"] >= 0.4
    assert item["student_fit_reason"]
    assert "fieldwork" in item["student_tags"] or "conservation" in item["student_tags"]
