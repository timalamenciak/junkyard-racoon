#!/usr/bin/env python3
"""Render a Matrix-friendly summary of the current daily digest."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, OUTPUT_DIR, load_json, load_yaml


def main() -> None:
    output_cfg = load_yaml(CONFIGS_DIR / "output.yaml")
    digest = load_json(OUTPUT_DIR / "daily_digest.json", default={})
    publish = load_json(OUTPUT_DIR / "bookstack_publish.json", default={})
    if not digest:
        raise SystemExit("No digest found. Run processing/daily_digest.py first.")

    lines = [f"Daily Lab Digest {digest.get('date', 'unknown')}"]
    relevant = digest.get("relevant_articles", [])
    grants = digest.get("relevant_grants", [])
    todos = digest.get("prioritized_todos", [])
    if relevant:
        lines.append(f"Relevant papers: {len(relevant)}")
        for article in relevant[:5]:
            lines.append(f"- {article.get('title', '')}")
    else:
        lines.append("Relevant papers: 0")

    lines.append(f"High-fit grants: {len(grants)}")
    lines.append(f"Priority tasks: {len(todos)}")

    if publish.get("articles_url") or publish.get("grants_url") or publish.get("tasks_url"):
        lines.append("")
        lines.append("BookStack:")
        for label, key in [("Articles", "articles_url"), ("Grants", "grants_url"), ("Tasks", "tasks_url")]:
            if publish.get(key):
                lines.append(f"  {label}: {publish[key]}")

    rendered = "\n".join(lines)
    print(rendered)
    summary_file = output_cfg.get("matrix", {}).get("summary_file", "matrix_digest.txt")
    (OUTPUT_DIR / summary_file).write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
