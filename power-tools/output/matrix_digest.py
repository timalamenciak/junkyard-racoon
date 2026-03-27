#!/usr/bin/env python3
"""Render a Matrix-friendly summary of the current daily digest, with Racoon Lab personality."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, OUTPUT_DIR, load_json, load_yaml


_DIVIDER = "─" * 36


def _score_pct(item: dict, key: str = "relevance_score") -> str:
    try:
        return f"{int(float(item.get(key, 0)) * 100)}%"
    except Exception:
        return ""


def main() -> None:
    output_cfg = load_yaml(CONFIGS_DIR / "output.yaml")
    digest = load_json(OUTPUT_DIR / "daily_digest.json", default={})
    publish = load_json(OUTPUT_DIR / "hedgedoc_publish.json", default={})
    static_publish = load_json(OUTPUT_DIR / "static_digest_publish.json", default={})
    if not digest:
        raise SystemExit("No digest found. Run processing/daily_digest.py first.")

    date = digest.get("date", "unknown")
    news = digest.get("relevant_news", [])
    articles = digest.get("relevant_articles", [])
    grants = digest.get("relevant_grants", [])
    todos = digest.get("prioritized_todos", [])
    jobs = digest.get("open_jobs", [])
    toots = digest.get("mastodon_toots", [])

    lines: list[str] = []

    # ── Header ──
    lines.append(f"🦝 Junkyard Racoon // {date}")
    lines.append(_DIVIDER)
    lines.append(
        "Another night in the dumpster. Here's what I dragged out for you."
    )
    lines.append("")

    # ── Top news ──
    lines.append("📰 Top stories:")
    top_news = news[:3]
    if top_news:
        for item in top_news:
            title = item.get("title", "").strip()
            summary = (item.get("summary", "") or "").strip()
            link = item.get("link", "").strip()
            lines.append(f"  • {title}")
            if summary:
                # Trim to a single readable sentence
                first_sentence = summary.split(".")[0].strip()
                if first_sentence and first_sentence != title:
                    lines.append(f"    {first_sentence}.")
            if link:
                lines.append(f"    {link}")
    else:
        lines.append("  Nothing broke the surface today. Quiet night.")
    lines.append("")

    # ── Articles ──
    if articles:
        lines.append(f"📄 Relevant papers ({len(articles)}):")
        for article in articles[:5]:
            score = _score_pct(article)
            title = article.get("title", "").strip()
            score_str = f" [{score}]" if score else ""
            lines.append(f"  • {title}{score_str}")
    else:
        lines.append("📄 Papers: nothing cleared the relevance bar today.")
    lines.append("")

    # ── Grants ──
    if grants:
        lines.append(f"💰 Grant leads ({len(grants)}):")
        for grant in grants[:3]:
            score = _score_pct(grant)
            title = grant.get("title", "").strip()
            score_str = f" [{score}]" if score else ""
            lines.append(f"  • {title}{score_str}")
    else:
        lines.append("💰 Grants: nothing new in the pile.")
    lines.append("")

    # ── Tasks ──
    high_todos = [t for t in todos if t.get("priority") in ("high", "urgent")]
    if high_todos:
        lines.append(f"✅ High-priority tasks ({len(high_todos)} of {len(todos)}):")
        for todo in high_todos[:5]:
            task = todo.get("task", "").strip()
            project = todo.get("project", "").strip()
            project_str = f" [{project}]" if project else ""
            lines.append(f"  • {task}{project_str}")
    elif todos:
        lines.append(f"✅ Tasks: {len(todos)} items queued, none flagged urgent.")
    else:
        lines.append("✅ Tasks: Obsidian was quiet tonight.")
    lines.append("")

    # ── Jobs ──
    lines.append(f"💼 Open jobs on the board: {len(jobs)}")
    lines.append("")

    # ── Links ──
    lines.append(_DIVIDER)
    if static_publish.get("public_url"):
        lines.append(f"🌐 Full digest: {static_publish['public_url']}")
    if publish.get("articles_url"):
        lines.append(f"📝 HedgeDoc articles: {publish['articles_url']}")
    if publish.get("grants_url"):
        lines.append(f"📝 HedgeDoc grants: {publish['grants_url']}")
    if publish.get("tasks_url"):
        lines.append(f"📝 HedgeDoc tasks: {publish['tasks_url']}")
    if publish.get("jobs_url"):
        lines.append(f"📝 HedgeDoc jobs: {publish['jobs_url']}")

    # ── Mastodon toots teaser ──
    if toots:
        lines.append("")
        lines.append(f"🐘 {len(toots)} Mastodon toots ready to go — check daily_digest.md")

    rendered = "\n".join(lines)
    print(rendered)
    summary_file = output_cfg.get("matrix", {}).get("summary_file", "matrix_digest.txt")
    (OUTPUT_DIR / summary_file).write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
