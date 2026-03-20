#!/usr/bin/env python3
"""Score grant opportunities for lab fit and urgency."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, INGEST_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.llm import chat_completion, extract_json_payload


def build_prompt(grants: list[dict], lab_profile: dict) -> list[dict[str, str]]:
    priority_areas = "\n".join(f"- {item}" for item in lab_profile.get("grant_priority_areas", []))
    scoring_rules = "\n".join(f"- {item}" for item in lab_profile.get("grant_scoring_rules", []))
    lines = []
    for idx, grant in enumerate(grants):
        lines.append(
            f"[{idx}] {grant.get('title', '')}\n"
            f"Source: {grant.get('source', '')}\n"
            f"Tags: {', '.join(grant.get('tags', []))}\n"
            f"Summary: {grant.get('summary', '')[:800]}\n"
            f"Link: {grant.get('link', '')}"
        )
    system = (
        "You are triaging grant opportunities for an ecology and restoration lab.\n"
        "Score each grant from 0.0 to 1.0 based on strategic fit, likely eligibility, and urgency.\n"
        "Return only JSON as a list of objects with keys: index, score, summary, rationale, next_step.\n\n"
        f"Priority areas:\n{priority_areas}\n\n"
        f"Scoring rules:\n{scoring_rules}"
    )
    user = "Grant opportunities:\n\n" + "\n\n".join(lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def main() -> None:
    ensure_data_dirs()
    lab_profile = load_yaml(CONFIGS_DIR / "lab_profile.yaml")
    payload = load_json(INGEST_DIR / "grant_opportunities.json", default={"items": []})
    grants = payload.get("items", [])
    if not grants:
        dump_json(PROCESSING_DIR / "scored_grants.json", {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": []})
        print("No grant opportunities to score")
        return

    response = chat_completion(build_prompt(grants, lab_profile), max_tokens=2600, temperature=0.0)
    scored = extract_json_payload(response)
    for item in scored:
        idx = item.get("index")
        if isinstance(idx, int) and 0 <= idx < len(grants):
            grants[idx]["relevance_score"] = float(item.get("score", 0.0))
            grants[idx]["llm_summary"] = item.get("summary", "")
            grants[idx]["rationale"] = item.get("rationale", "")
            grants[idx]["next_step"] = item.get("next_step", "")

    relevant = [item for item in grants if item.get("relevance_score", 0.0) >= float(lab_profile.get("grant_relevance_threshold", 0.65))]
    relevant.sort(key=lambda item: item.get("relevance_score", 0.0), reverse=True)
    dump_json(
        PROCESSING_DIR / "scored_grants.json",
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": grants,
            "relevant_items": relevant,
        },
    )
    print(f"Scored {len(grants)} grants; kept {len(relevant)} relevant")


if __name__ == "__main__":
    main()
