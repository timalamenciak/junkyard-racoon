#!/usr/bin/env python3
"""Score grant opportunities for lab fit and urgency."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, INGEST_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.llm import chat_completion, extract_json_payload
from common.runtime import is_test_mode

BATCH_SIZE = 10


def build_prompt(grants: list[dict], lab_profile: dict) -> list[dict[str, str]]:
    priority_areas = "\n".join(f"- {item}" for item in lab_profile.get("grant_priority_areas", []))
    scoring_rules = "\n".join(f"- {item}" for item in lab_profile.get("grant_scoring_rules", []))
    lines = []
    for idx, grant in enumerate(grants):
        meta_parts = [f"Source: {grant.get('source', '')}"]
        if grant.get("program"):
            meta_parts.append(f"Program: {grant.get('program', '')}")
        if grant.get("amount"):
            meta_parts.append(f"Amount: {grant.get('amount', '')}")
        if grant.get("deadline"):
            meta_parts.append(f"Deadline: {grant.get('deadline', '')}")
        if grant.get("status"):
            meta_parts.append(f"Status: {grant.get('status', '')}")
        lines.append(
            f"[{idx}] {grant.get('title', '')}\n"
            + "\n".join(meta_parts) + "\n"
            f"Tags: {', '.join(grant.get('tags', []))}\n"
            f"Notes/Summary: {grant.get('summary', '')[:800]}\n"
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


def score_grants_for_test_mode(grants: list[dict]) -> list[dict]:
    for idx, grant in enumerate(grants):
        score = max(0.5, 0.85 - (idx * 0.1))
        grant["relevance_score"] = round(score, 2)
        grant["llm_summary"] = f"Sample grant summary for {grant.get('title', 'untitled grant')}."
        grant["rationale"] = "Generated in test mode without calling the LLM."
        grant["next_step"] = "Decide whether to assign a quick eligibility review."
    return grants


def score_grants_llm(grants: list[dict], lab_profile: dict) -> None:
    """Score grants in batches, writing scores back in-place. Logs warnings on partial failures."""
    for batch_start in range(0, len(grants), BATCH_SIZE):
        batch = grants[batch_start : batch_start + BATCH_SIZE]
        try:
            response = chat_completion(build_prompt(batch, lab_profile), max_tokens=2600, temperature=0.0)
            scored = extract_json_payload(response)
        except Exception as exc:
            print(
                f"Warning: LLM scoring failed for grants {batch_start}-{batch_start + len(batch) - 1}: {exc}",
                file=sys.stderr,
            )
            continue
        if not isinstance(scored, list):
            print(
                f"Warning: LLM scoring returned non-list payload for grants {batch_start}-{batch_start + len(batch) - 1}",
                file=sys.stderr,
            )
            continue
        for item in scored:
            if not isinstance(item, dict):
                print(
                    f"Warning: skipping malformed grant score item in batch {batch_start}-{batch_start + len(batch) - 1}: {item!r}",
                    file=sys.stderr,
                )
                continue
            local_idx = item.get("index")
            if isinstance(local_idx, int) and 0 <= local_idx < len(batch):
                g = grants[batch_start + local_idx]
                g["relevance_score"] = float(item.get("score", 0.0))
                g["llm_summary"] = item.get("summary", "")
                g["rationale"] = item.get("rationale", "")
                g["next_step"] = item.get("next_step", "")


def main() -> None:
    ensure_data_dirs()
    lab_profile = load_yaml(CONFIGS_DIR / "lab_profile.yaml")
    payload = load_json(INGEST_DIR / "grant_opportunities.json", default={"items": []})
    grants = payload.get("items", [])
    if not grants:
        dump_json(PROCESSING_DIR / "scored_grants.json", {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": []})
        print("No grant opportunities to score")
        return

    if is_test_mode():
        grants = score_grants_for_test_mode(grants)
    else:
        score_grants_llm(grants, lab_profile)

    threshold = float(lab_profile.get("grant_relevance_threshold", 0.65))
    relevant = [
        item for item in grants
        if item.get("always_surface") or item.get("relevance_score", 0.0) >= threshold
    ]
    # Sort: manual/always-surface grants first (sorted by deadline), then by score
    def _sort_key(item: dict):
        is_manual = item.get("always_surface", False)
        deadline = item.get("deadline", "9999-99-99")
        score = item.get("relevance_score", 0.0)
        return (0 if is_manual else 1, deadline, -score)
    relevant.sort(key=_sort_key)
    dump_json(
        PROCESSING_DIR / "scored_grants.json",
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": grants,
            "relevant_items": relevant,
            "test_mode": is_test_mode(),
        },
    )
    print(f"Scored {len(grants)} grants; kept {len(relevant)} relevant")


if __name__ == "__main__":
    main()
