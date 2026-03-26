#!/usr/bin/env python3
"""Build a daily digest markdown artifact from processed inputs."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import INGEST_DIR, OUTPUT_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_json


def main() -> None:
    ensure_data_dirs()
    scored_articles = load_json(PROCESSING_DIR / "scored_articles.json", default={})
    scored_grants = load_json(PROCESSING_DIR / "scored_grants.json", default={})
    scored_jobs = load_json(PROCESSING_DIR / "scored_jobs.json", default={})
    todos = load_json(PROCESSING_DIR / "obsidian_todos.json", default={})
    publications = load_json(INGEST_DIR / "collaborator_publications.json", default={})
    news = load_json(INGEST_DIR / "news_items.json", default={})
    jobs = load_json(INGEST_DIR / "job_openings.json", default={})
    date_str = datetime.date.today().isoformat()

    relevant_news = news.get("relevant_items", [])[:10]
    relevant_articles = scored_articles.get("relevant_items", [])[:10]
    relevant_grants = scored_grants.get("relevant_items", [])[:10]
    prioritized_todos = todos.get("items", [])[:20]
    collaborator_items = [item for item in publications.get("items", []) if not item.get("error")][:10]
    open_jobs = scored_jobs.get("items", []) or jobs.get("items", [])

    lines = [f"# Daily Lab Digest - {date_str}", ""]
    lines.append("## Research News")
    if relevant_news:
        for item in relevant_news:
            lines.append(f"- {item.get('title', '')}")
            lines.append(f"  {item.get('summary', '')}")
            lines.append(f"  {item.get('link', '')}")
    else:
        lines.append("- No relevant news items today.")

    lines.append("")
    lines.append("## Relevant Papers")
    if relevant_articles:
        for article in relevant_articles:
            lines.append(f"- {article.get('title', '')} ({int(article.get('relevance_score', 0) * 100)}%)")
            lines.append(f"  {article.get('llm_summary', article.get('summary', ''))}")
            if article.get("recommended_action"):
                lines.append(f"  Recommended action: {article.get('recommended_action', '')}")
            lines.append(f"  {article.get('link', '')}")
    else:
        lines.append("- No high-relevance papers today.")

    lines.append("")
    lines.append("## Grant Opportunities Worth Reviewing")
    if relevant_grants:
        for grant in relevant_grants:
            lines.append(f"- {grant.get('title', '')} ({int(grant.get('relevance_score', 0) * 100)}%)")
            lines.append(f"  {grant.get('llm_summary', grant.get('summary', ''))}")
            if grant.get("next_step"):
                lines.append(f"  Next step: {grant.get('next_step', '')}")
            lines.append(f"  {grant.get('link', '')}")
    else:
        lines.append("- No high-fit grant opportunities today.")

    lines.append("")
    lines.append("## Collaborator Publications")
    if collaborator_items:
        for item in collaborator_items:
            lines.append(f"- {item.get('collaborator', 'Unknown collaborator')}: {item.get('title', '')}")
            lines.append(f"  {item.get('link', '')}")
    else:
        lines.append("- No recent collaborator publications captured.")

    lines.append("")
    lines.append("## Project Todos Requiring Attention")
    if prioritized_todos:
        for todo in prioritized_todos:
            lines.append(f"- [{todo.get('priority', 'medium')}] {todo.get('task', '')}")
            if todo.get("project"):
                lines.append(f"  Project: {todo.get('project', '')}")
            if todo.get("owner_guess"):
                lines.append(f"  Likely owner: {todo.get('owner_guess', '')}")
            if todo.get("deadline_guess"):
                lines.append(f"  Deadline cue: {todo.get('deadline_guess', '')}")
            if todo.get("impact") or todo.get("effort"):
                lines.append(f"  Impact/Effort: {todo.get('impact', 'unknown')}/{todo.get('effort', 'unknown')}")
            if todo.get("rationale"):
                lines.append(f"  Why now: {todo.get('rationale', '')}")
            lines.append(f"  {todo.get('note', '')}")
    else:
        lines.append("- No priority project tasks extracted from Obsidian notes.")

    lines.append("")
    lines.append("## Job Openings")
    if open_jobs:
        academic_count = len([item for item in open_jobs if item.get("category") == "academic"])
        conservation_count = len([item for item in open_jobs if item.get("category") == "conservation"])
        lines.append(f"- Conservation jobs captured: {conservation_count}")
        lines.append(f"- Academic biodiversity/restoration jobs captured: {academic_count}")
    else:
        lines.append("- No job openings extracted from tagged job newsletters.")

    markdown = "\n".join(lines) + "\n"
    digest_payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "date": date_str,
        "markdown": markdown,
        "relevant_news": relevant_news,
        "relevant_articles": relevant_articles,
        "relevant_grants": relevant_grants,
        "prioritized_todos": prioritized_todos,
        "collaborator_publications": collaborator_items,
        "open_jobs": open_jobs,
        "schema_version": 4,
    }
    dump_json(OUTPUT_DIR / "daily_digest.json", digest_payload)
    (OUTPUT_DIR / "daily_digest.md").write_text(markdown, encoding="utf-8")
    print(f"Wrote digest to {OUTPUT_DIR / 'daily_digest.md'}")


if __name__ == "__main__":
    main()
