#!/usr/bin/env python3
"""Run the full nightly power-tools pipeline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent

STEPS = [
    ("ingest journals", ROOT / "ingest" / "rss_journals.py"),
    ("ingest grants", ROOT / "ingest" / "grant_opportunities.py"),
    ("ingest collaborator publications", ROOT / "ingest" / "collaborator_publications.py"),
    ("score articles", ROOT / "processing" / "score_articles.py"),
    ("score grants", ROOT / "processing" / "score_grants.py"),
    ("extract obsidian todos", ROOT / "processing" / "obsidian_todos.py"),
    ("build daily digest", ROOT / "processing" / "daily_digest.py"),
    ("publish bookstack", ROOT / "output" / "publish_bookstack.py"),
    ("render matrix digest", ROOT / "output" / "matrix_digest.py"),
    ("generate podcast script", ROOT / "output" / "podcast_script.py"),
]


def main() -> None:
    for label, script in STEPS:
        print(f"[nightly-run] {label}: {script}")
        completed = subprocess.run([sys.executable, str(script)], cwd=str(ROOT.parent), check=False)
        if completed.returncode != 0:
            raise SystemExit(f"Step failed: {label}")
    print("[nightly-run] complete")


if __name__ == "__main__":
    main()
