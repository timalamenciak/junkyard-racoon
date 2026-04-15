#!/usr/bin/env python3
"""Build a daily digest markdown artifact from processed inputs."""

from __future__ import annotations

import datetime
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import INGEST_DIR, OUTPUT_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_json
from common.llm import chat_completion, extract_json_payload
from common.runtime import is_test_mode


def _strip_html(text: str) -> str:
    """Strip HTML tags and decode common entities from a string."""
    text = re.sub(r"<[^>]+>", " ", text)
    entities = {"&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'", "&nbsp;": " ", "&#x2F;": "/"}
    for entity, char in entities.items():
        text = text.replace(entity, char)
    return re.sub(r"\s{2,}", " ", text).strip()


def _truncate_plain_text(text: str, limit: int) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    shortened = value[:limit].rsplit(" ", 1)[0].strip()
    return f"{shortened}..."


def build_article_toot(article: dict) -> str:
    title = _truncate_plain_text(article.get("title", "New paper for the lab radar"), 180)
    summary = _truncate_plain_text(article.get("llm_summary") or article.get("summary", ""), 180)
    score = ""
    try:
        score = f" ({int(float(article.get('relevance_score', 0.0)) * 100)}% lab fit)"
    except Exception:
        score = ""
    action = _truncate_plain_text(article.get("recommended_action", ""), 90)
    link = str(article.get("link", "")).strip()

    lines = [f"New journal article on the Racoon Lab radar: {title}{score}."]
    if summary:
        lines.append(summary)
    if action:
        lines.append(f"Next step: {action}.")
    lines.append("#EcologyResearch #RestorationEcology #ConservationScience")
    if link:
        lines.append(link)
    return "\n".join(line for line in lines if line).strip()


def ensure_article_toots(toots: list[str], relevant_articles: list[dict]) -> list[str]:
    if not relevant_articles:
        return toots[:5]

    article_titles = {str(article.get("title", "")).strip().lower() for article in relevant_articles if str(article.get("title", "")).strip()}
    article_links = {str(article.get("link", "")).strip() for article in relevant_articles if str(article.get("link", "")).strip()}
    for toot in toots:
        lowered = toot.lower()
        if any(title and title in lowered for title in article_titles):
            return toots[:5]
        if any(link and link in toot for link in article_links):
            return toots[:5]

    return [build_article_toot(relevant_articles[0]), *toots][:5]


def generate_mastodon_toots(digest_markdown: str, relevant_articles: list[dict]) -> list[str]:
    """Ask the LLM to generate 5 Mastodon toots from the daily digest."""
    clean_digest = _strip_html(digest_markdown)
    system = (
        "You are the Junkyard Racoon, a scrappy and curious research lab mascot who actually reads the literature.\n"
        "Your Mastodon account shares conservation science news, research finds, job tips, and grant alerts.\n"
        "Tone: witty, nature-forward, genuinely informative. Not corporate. Not preachy.\n"
        "Generate exactly 5 Mastodon toots based on the daily digest. Each toot must:\n"
        "- Be under 500 characters including hashtags\n"
        "- Be self-contained and interesting to the conservation science community\n"
        "- Include 2-4 relevant hashtags\n"
        "- Cover a distinct angle such as new research, job/career tip, grant alert, news, or lab insight\n"
        "- Use plain text only with no HTML tags, Markdown links, or angle brackets\n"
        "- Where you reference a specific article or source, include its plain URL on its own line\n"
        "- If there are papers in the Relevant Papers section, at least one toot must clearly reference one of those papers by title or URL\n"
        "Return only JSON as a list of exactly 5 plain-text strings."
    )
    user = f"Based on this daily digest, what are 5 Mastodon toots that the Junkyard Racoon account could toot?\n\n{clean_digest[:4000]}"
    response = chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=1500,
        temperature=0.6,
    )
    parsed = extract_json_payload(response)
    if isinstance(parsed, list):
        toots = [str(t).strip() for t in parsed if str(t).strip()][:5]
        return ensure_article_toots(toots, relevant_articles)
    return []


def sample_mastodon_toots() -> list[str]:
    return [
        "New in restoration ecology: participatory planning with AI decision-support agents is showing real promise for community-led projects. The future of restoration might be collaborative by design. #RestorationEcology #ConservationScience #AI",
        "Hiring alert for conservation folks: several field positions open this month with strong student fit. Check the Racoon Lab digest for the full list. #ConservationJobs #Ecology #Fieldwork",
        "Grant radar: a new biodiversity catalyst fund just opened with a close deadline. High fit for restoration and community engagement work. Worth a look. #GrantAlert #Biodiversity #ResearchFunding",
        "Reading: a paper on knowledge co-production in human-dimensions of restoration scored 89% relevance this week. The lab is flagging it for journal club. #HumanDimensions #EcologyResearch",
        "The Narwhal and Mongabay both dropped pieces this week on policy shifts affecting Great Lakes restoration. Worth reading alongside the science. #ConservationPolicy #GreatLakes #RestorationNews",
    ]


def main() -> None:
    ensure_data_dirs()
    scored_articles = load_json(PROCESSING_DIR / "scored_articles.json", default={})
    scored_grants = load_json(PROCESSING_DIR / "scored_grants.json", default={})
    scored_news = load_json(PROCESSING_DIR / "scored_news.json", default={})
    scored_jobs = load_json(PROCESSING_DIR / "scored_jobs.json", default={})
    publications = load_json(INGEST_DIR / "collaborator_publications.json", default={})
    jobs = load_json(INGEST_DIR / "job_openings.json", default={})
    date_str = datetime.date.today().isoformat()

    relevant_news = scored_news.get("relevant_items", [])[:10]
    relevant_articles = scored_articles.get("relevant_items", [])[:10]
    relevant_grants = scored_grants.get("relevant_items", [])[:10]
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
    lines.append("## Job Openings")
    if open_jobs:
        academic_count = len([item for item in open_jobs if item.get("category") == "academic"])
        conservation_count = len([item for item in open_jobs if item.get("category") == "conservation"])
        lines.append(f"- Conservation jobs captured: {conservation_count}")
        lines.append(f"- Academic biodiversity/restoration jobs captured: {academic_count}")
    else:
        lines.append("- No job openings extracted from tagged job newsletters.")

    markdown = "\n".join(lines) + "\n"

    if is_test_mode():
        mastodon_toots = sample_mastodon_toots()
    else:
        try:
            mastodon_toots = generate_mastodon_toots(markdown, relevant_articles)
        except Exception as exc:
            print(f"Warning: Mastodon toot generation failed: {exc}", file=sys.stderr)
            mastodon_toots = []
    mastodon_toots = ensure_article_toots(mastodon_toots, relevant_articles)

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

    homepage_items = []
    for article in relevant_articles:
        try:
            score_pct = int(float(article.get("relevance_score", 0)) * 100)
        except Exception:
            score_pct = 0
        homepage_items.append({
            "title": _truncate_plain_text(article.get("title", ""), 60),
            "badge": f"paper {score_pct}%",
            "link": str(article.get("link", "")),
        })
    for news_item in relevant_news:
        try:
            score_pct = int(float(news_item.get("relevance_score", 0)) * 100)
        except Exception:
            score_pct = 0
        badge = f"news {score_pct}%" if score_pct else "news"
        homepage_items.append({
            "title": _truncate_plain_text(news_item.get("title", ""), 60),
            "badge": badge,
            "link": str(news_item.get("link", "")),
        })
    for grant in relevant_grants:
        try:
            score_pct = int(float(grant.get("relevance_score", 0)) * 100)
        except Exception:
            score_pct = 0
        homepage_items.append({
            "title": _truncate_plain_text(grant.get("title", ""), 60),
            "badge": f"grant {score_pct}%",
            "link": str(grant.get("link", "")),
        })

    def _score_from_badge(item: dict) -> int:
        try:
            return int(item["badge"].rsplit(" ", 1)[-1].rstrip("%"))
        except Exception:
            return 0

    homepage_items.sort(key=_score_from_badge, reverse=True)

    homepage_feed = {
        "date": date_str,
        "generated_at": digest_payload["generated_at"],
        "articles_count": len(relevant_articles),
        "news_count": len(relevant_news),
        "grants_count": len(relevant_grants),
        "items": homepage_items,
    }
    dump_json(OUTPUT_DIR / "homepage_feed.json", homepage_feed)
    print(f"Wrote homepage feed to {OUTPUT_DIR / 'homepage_feed.json'}")


if __name__ == "__main__":
    main()
