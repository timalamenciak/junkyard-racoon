#!/usr/bin/env python3
"""Score news items for lab relevance using the LLM."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, INGEST_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.llm import chat_completion, extract_json_payload
from common.runtime import is_test_mode

BATCH_SIZE = 15


def build_prompt(items: list[dict], lab_profile: dict) -> list[dict[str, str]]:
    interests = "\n".join(f"- {item}" for item in lab_profile.get("research_interests", []))
    scoring_rules = "\n".join(f"- {item}" for item in lab_profile.get("news_scoring_rules", []))
    lines = []
    for idx, item in enumerate(items):
        lines.append(
            f"[{idx}] {item.get('title', '')}\n"
            f"Source: {item.get('source', '')}\n"
            f"Summary: {item.get('summary', '')[:600]}"
        )
    system = (
        "You are screening news items for a restoration and conservation ecology lab.\n"
        "Score each item from 0.0 to 1.0 for relevance to the lab's work.\n"
        "For relevant items, write a brief one-sentence summary of why it matters to the lab.\n"
        "Return only JSON as a list of objects with keys: index, score, summary.\n\n"
        f"Lab interests:\n{interests}\n\n"
        f"Scoring rules:\n{scoring_rules}"
    )
    user = "News items:\n\n" + "\n\n".join(lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def score_news_for_test_mode(items: list[dict]) -> list[dict]:
    for idx, item in enumerate(items):
        item["relevance_score"] = round(max(0.5, 0.85 - (idx * 0.1)), 2)
        item["llm_summary"] = f"Sample summary for {item.get('title', 'untitled news item')}."
    return items


def score_news_llm(items: list[dict], lab_profile: dict) -> None:
    """Score news items in batches, writing scores back in-place."""
    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start : batch_start + BATCH_SIZE]
        try:
            response = chat_completion(build_prompt(batch, lab_profile), max_tokens=2000, temperature=0.0)
            scored = extract_json_payload(response)
        except Exception as exc:
            print(
                f"Warning: LLM scoring failed for news {batch_start}-{batch_start + len(batch) - 1}: {exc}",
                file=sys.stderr,
            )
            continue
        if not isinstance(scored, list):
            print(
                f"Warning: LLM scoring returned non-list payload for news {batch_start}-{batch_start + len(batch) - 1}",
                file=sys.stderr,
            )
            continue
        for scored_item in scored:
            if not isinstance(scored_item, dict):
                continue
            local_idx = scored_item.get("index")
            if isinstance(local_idx, int) and 0 <= local_idx < len(batch):
                n = items[batch_start + local_idx]
                n["relevance_score"] = float(scored_item.get("score", 0.0))
                n["llm_summary"] = scored_item.get("summary", "")


def main() -> None:
    ensure_data_dirs()
    lab_profile = load_yaml(CONFIGS_DIR / "lab_profile.yaml")
    payload = load_json(INGEST_DIR / "news_items.json", default={"items": []})
    items = payload.get("items", [])
    if not items:
        dump_json(
            PROCESSING_DIR / "scored_news.json",
            {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": [], "relevant_items": []},
        )
        print("No news items to score")
        return

    if is_test_mode():
        items = score_news_for_test_mode(items)
    else:
        score_news_llm(items, lab_profile)

    threshold = float(lab_profile.get("news_relevance_threshold", 0.60))
    relevant = [item for item in items if item.get("relevance_score", 0.0) >= threshold]
    relevant.sort(key=lambda item: item.get("relevance_score", 0.0), reverse=True)
    dump_json(
        PROCESSING_DIR / "scored_news.json",
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": items,
            "relevant_items": relevant,
            "test_mode": is_test_mode(),
        },
    )
    print(f"Scored {len(items)} news items; kept {len(relevant)} relevant")


if __name__ == "__main__":
    main()
