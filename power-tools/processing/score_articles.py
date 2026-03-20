#!/usr/bin/env python3
"""Score journal articles for lab relevance and summarize the relevant ones."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, INGEST_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.llm import chat_completion, extract_json_payload
from common.runtime import is_test_mode


def build_prompt(articles: list[dict], lab_profile: dict) -> list[dict[str, str]]:
    interests = "\n".join(f"- {item}" for item in lab_profile.get("research_interests", []))
    scoring_rules = "\n".join(f"- {item}" for item in lab_profile.get("article_scoring_rules", []))
    lines = []
    for idx, article in enumerate(articles):
        lines.append(
            f"[{idx}] {article.get('title', '')}\n"
            f"Feed: {article.get('feed', '')}\n"
            f"Tags: {', '.join(article.get('tags', []))}\n"
            f"Summary: {article.get('summary', '')[:700]}"
        )
    system = (
        "You are screening journal articles for a restoration and conservation ecology lab.\n"
        "Score each article from 0.0 to 1.0 for practical relevance to current lab work.\n"
        "For articles worth surfacing, provide a short summary, rationale, and recommended action.\n"
        "Return only JSON as a list of objects with keys: index, score, summary, rationale, recommended_action.\n\n"
        f"Lab interests:\n{interests}\n\n"
        f"Scoring rules:\n{scoring_rules}"
    )
    user = "Articles:\n\n" + "\n\n".join(lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def score_articles_for_test_mode(articles: list[dict]) -> list[dict]:
    for idx, article in enumerate(articles):
        score = max(0.55, 0.9 - (idx * 0.1))
        article["relevance_score"] = round(score, 2)
        article["llm_summary"] = f"Sample summary for {article.get('title', 'untitled article')}."
        article["rationale"] = "Generated in test mode without calling the LLM."
        article["recommended_action"] = "Review fit for the next lab meeting."
    return articles


def main() -> None:
    ensure_data_dirs()
    lab_profile = load_yaml(CONFIGS_DIR / "lab_profile.yaml")
    payload = load_json(INGEST_DIR / "journal_articles.json", default={"items": []})
    articles = payload.get("items", [])
    if not articles:
        dump_json(PROCESSING_DIR / "scored_articles.json", {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": []})
        print("No journal articles to score")
        return

    if is_test_mode():
        articles = score_articles_for_test_mode(articles)
    else:
        response = chat_completion(build_prompt(articles, lab_profile), max_tokens=3000, temperature=0.0)
        scored = extract_json_payload(response)
        for item in scored:
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(articles):
                articles[idx]["relevance_score"] = float(item.get("score", 0.0))
                articles[idx]["llm_summary"] = item.get("summary", "")
                articles[idx]["rationale"] = item.get("rationale", "")
                articles[idx]["recommended_action"] = item.get("recommended_action", "")

    relevant = [a for a in articles if a.get("relevance_score", 0.0) >= float(lab_profile.get("article_relevance_threshold", 0.75))]
    relevant.sort(key=lambda item: item.get("relevance_score", 0.0), reverse=True)
    dump_json(
        PROCESSING_DIR / "scored_articles.json",
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": articles,
            "relevant_items": relevant,
            "test_mode": is_test_mode(),
        },
    )
    print(f"Scored {len(articles)} articles; kept {len(relevant)} relevant")


if __name__ == "__main__":
    main()
