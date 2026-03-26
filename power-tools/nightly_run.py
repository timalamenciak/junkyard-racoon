#!/usr/bin/env python3
"""Run the full nightly power-tools pipeline."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from common.runtime import TEST_MODE_ENV


ROOT = Path(__file__).resolve().parent

STEPS = [
    {"label": "ingest collaborator publications", "script": ROOT / "ingest" / "collaborator_publications.py"},
    {"label": "ingest gmail email", "script": ROOT / "ingest" / "gmail_imap_bridge.py", "allow_failure": True},
    {"label": "ingest journals (rss + email merge)", "script": ROOT / "ingest" / "rss_journals.py"},
    {"label": "ingest grants (rss + email merge)", "script": ROOT / "ingest" / "grant_opportunities.py"},
    {"label": "ingest research news (rss + email merge)", "script": ROOT / "ingest" / "research_news.py"},
    {"label": "ingest jobs (email merge)", "script": ROOT / "ingest" / "job_openings.py"},
    {"label": "score articles", "script": ROOT / "processing" / "score_articles.py"},
    {"label": "score grants", "script": ROOT / "processing" / "score_grants.py"},
    {"label": "extract obsidian todos", "script": ROOT / "processing" / "obsidian_todos.py"},
    {"label": "build daily digest", "script": ROOT / "processing" / "daily_digest.py"},
    {"label": "publish hedgedoc", "script": ROOT / "output" / "publish_hedgedoc.py", "allow_failure": True},
    {"label": "publish static digest", "script": ROOT / "output" / "publish_static_digest.py"},
    {"label": "render matrix digest", "script": ROOT / "output" / "matrix_digest.py"},
    {"label": "generate podcast script", "script": ROOT / "output" / "podcast_script.py"},
]


def python_command() -> list[str]:
    launcher = shutil.which("py")
    if os.name == "nt" and launcher:
        return [launcher, "-3"]
    return [sys.executable]


def main() -> None:
    test_mode = "--test" in sys.argv[1:]
    env = os.environ.copy()
    python_cmd = python_command()
    if test_mode:
        env[TEST_MODE_ENV] = "1"
        print("[nightly-run] test mode enabled; external side effects will be skipped")

    for step in STEPS:
        label = str(step["label"])
        script = Path(step["script"])
        allow_failure = bool(step.get("allow_failure", False))
        print(f"[nightly-run] {label}: {script}")
        completed = subprocess.run(
            python_cmd + [str(script)] + (["--test"] if test_mode else []),
            cwd=str(ROOT.parent),
            env=env,
            check=False,
        )
        if completed.returncode != 0:
            if allow_failure:
                print(
                    f"[nightly-run] WARNING: step failed but pipeline will continue: {label} (exit {completed.returncode})",
                    file=sys.stderr,
                )
                continue
            raise SystemExit(f"Step failed: {label}")
    print("[nightly-run] complete")


if __name__ == "__main__":
    main()
