#!/usr/bin/env python3
"""Build a daily digest markdown artifact from processed inputs."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import INGEST_DIR, OUTPUT_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_json
from common.llm import chat_completion, extract_json_payload
from common.runtime import is_test_mode


def generate_mastodon_toots(digest_markdown: str) -> list[str]:
    """Ask the LLM to generate 5 Mastodon toots from the daily digest."""
    system = (
        "You are the Junkyard Racoon — a scrappy, curious research lab mascot who actually reads the literature.\n"
        "Your Mastodon account shares conservation science news, research finds, job tips, and grant alerts.\n"
        "Tone: witty, nature-forward, genuinely informative. Not corporate. Not preachy.\n"
        "Generate exactly 5 Mastodon toots based on the daily digest. Each toot must:\n"
        "- Be under 500 characters (including hashtags)\n"
        "- Be self-contained and interesting to the conservation science community\n"
        "- Include 2-4 relevant hashtags\n"
        "- Cover a distinct angle: new research, job/career tip, grant alert, news, or lab insight\n"
        "Return only JSON as a list of exactly 5 strings."
    )
    user = f"Based on this daily digest, what are 5 Mastodon toots that the Junkyard Racoon account could toot?\n\n{digest_markdown[:4000]}"
    response = chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=1500,
        temperature=0.6,
    )
    parsed = extract_json_payload(response)
    if isinstance(parsed, list):
        return [str(t).strip() for t in parsed if str(t).strip()][:5]
    return []


def sample_mastodon_toots() -> list[str]:
    return [
        "New in restoration ecology: participatory planning with AI decision-support agents is showing real promise for community-led projects. The future of restoration might be collaborative by design. 🌿 #RestorationEcology #ConservationScience #AI",
        "Hiring alert for conservation folks: several field positions open this month with strong student fit. Check the Racoon Lab digest for the full list. 🦝 #ConservationJobs #Ecology #Fieldwork",
        "Grant radar: a new biodiversity catalyst fund just opened with a close deadline. High fit for restoration + community engagement work. Worth a look. 💰 #GrantAlert #Biodiversity #ResearchFunding",
        "Reading: a paper on knowledge co-production in human-dimensions of restoration scored 89% relevance this week. The lab is flagging it for journal club. 📚 #HumanDimensions #EcologyResearch",
        "The Narwhal and Mongabay both dropped pieces this week on policy shifts affecting Great Lakes restoration. Worth reading alongside the science. 🏞️ #ConservationPolicy #GreatLakes #RestorationNews",
    ]


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
    open_jobs = scored_jobs.get("relevant_items", []) or jobs.get("items", [])

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

    # Generate Mastodon toots from the full digest
    if is_test_mode():
        mastodon_toots = sample_mastodon_toots()
    else:
        try:
            mastodon_toots = generate_mastodon_toots(markdown)
        except Exception as exc:
            print(f"Warning: Mastodon toot generation failed: {exc}", file=sys.stderr)
            mastodon_toots = []

    if mastodon_toots:
        lines.append("")
        lines.append("## Mastodon Toots")
        for i, toot in enumerate(mastodon_toots, 1):
            lines.append(f"\n### Toot {i}")
            lines.append(toot)
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
        "mastodon_toots": mastodon_toots,
        "schema_version": 5,
    }
    dump_json(OUTPUT_DIR / "daily_digest.json", digest_payload)
    (OUTPUT_DIR / "daily_digest.md").write_text(markdown, encoding="utf-8")
    print(f"Wrote digest to {OUTPUT_DIR / 'daily_digest.md'}")
    if mastodon_toots:
        print(f"Generated {len(mastodon_toots)} Mastodon toots")


if __name__ == "__main__":
    main()
