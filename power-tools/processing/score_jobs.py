#!/usr/bin/env python3
"""Score job openings for student fit and normalize compensation hints."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, INGEST_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.llm import chat_completion, extract_json_payload
from common.runtime import is_test_mode


BATCH_SIZE = 3
DEFAULT_THRESHOLD = 0.80


def build_prompt(jobs: list[dict], lab_profile: dict) -> list[dict[str, str]]:
    interests = "\n".join(f"- {item}" for item in lab_profile.get("research_interests", []))
    lines = []
    for idx, job in enumerate(jobs):
        lines.append(
            f"[{idx}] {job.get('title', '')}\n"
            f"Organization: {job.get('organization', '')}\n"
            f"Location: {job.get('location', '')}\n"
            f"Pay raw: {job.get('pay', '')}\n"
            f"Posted: {job.get('posted_date', '')}\n"
            f"Deadline: {job.get('application_deadline', '')}\n"
            f"Category: {job.get('category', '')}\n"
            f"Summary: {job.get('summary', '')[:220]}\n"
            f"Link: {job.get('link', '')}"
        )
    system = (
        "You are triaging conservation and academic jobs for student relevance in a restoration and conservation lab.\n"
        "For each job, infer a cleaner pay_normalized string when possible, a short pay_notes string when pay is ambiguous,\n"
        "and score student relevance from 0.0 to 1.0. Add a brief student_fit_reason and a small list of tags.\n"
        "Use tags such as undergrad, msc, phd, postdoc, fieldwork, remote, canada, policy, restoration, conservation.\n"
        "Return only JSON as a list of objects with keys: index, pay_normalized, pay_notes, student_relevance_score, student_fit_reason, student_tags.\n\n"
        f"Lab interests:\n{interests}"
    )
    user = "Jobs:\n\n" + "\n\n".join(lines)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def score_jobs_for_test_mode(jobs: list[dict]) -> list[dict]:
    for idx, job in enumerate(jobs):
        score = max(0.4, 0.9 - (idx * 0.08))
        job["pay_normalized"] = job.get("pay", "")
        job["pay_notes"] = "Copied from scraped/email compensation text."
        job["student_relevance_score"] = round(score, 2)
        job["student_fit_reason"] = "Sample fit rationale generated in test mode."
        tags = []
        haystack = f"{job.get('title', '')} {job.get('summary', '')} {job.get('location', '')}".lower()
        for token in ("undergrad", "msc", "phd", "postdoc", "fieldwork", "remote", "canada", "restoration", "conservation"):
            if token in haystack:
                tags.append(token)
        job["student_tags"] = tags[:5] or ["conservation"]
    return jobs


def score_jobs_llm(jobs: list[dict], lab_profile: dict) -> None:
    for batch_start in range(0, len(jobs), BATCH_SIZE):
        batch = jobs[batch_start : batch_start + BATCH_SIZE]
        try:
            response = chat_completion(build_prompt(batch, lab_profile), max_tokens=2600, temperature=0.0)
            scored = extract_json_payload(response)
        except Exception as exc:
            print(
                f"Warning: LLM scoring failed for jobs {batch_start}-{batch_start + len(batch) - 1}: {exc}",
                file=sys.stderr,
            )
            continue
        for item in scored:
            local_idx = item.get("index")
            if isinstance(local_idx, int) and 0 <= local_idx < len(batch):
                job = jobs[batch_start + local_idx]
                job["pay_normalized"] = item.get("pay_normalized", "")
                job["pay_notes"] = item.get("pay_notes", "")
                job["student_relevance_score"] = float(item.get("student_relevance_score", 0.0))
                job["student_fit_reason"] = item.get("student_fit_reason", "")
                tags = item.get("student_tags", [])
                job["student_tags"] = [str(value).strip() for value in tags if str(value).strip()]


def main() -> None:
    ensure_data_dirs()
    lab_profile = load_yaml(CONFIGS_DIR / "lab_profile.yaml")
    payload = load_json(INGEST_DIR / "job_openings.json", default={"items": []})
    jobs = payload.get("items", [])
    if not jobs:
        dump_json(PROCESSING_DIR / "scored_jobs.json", {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": []})
        print("No job openings to score")
        return

    if is_test_mode():
        jobs = score_jobs_for_test_mode(jobs)
    else:
        score_jobs_llm(jobs, lab_profile)

    jobs.sort(key=lambda item: item.get("student_relevance_score", 0.0), reverse=True)
    threshold = float(lab_profile.get("job_relevance_threshold", DEFAULT_THRESHOLD))
    relevant = [item for item in jobs if item.get("student_relevance_score", 0.0) >= threshold]
    dump_json(
        PROCESSING_DIR / "scored_jobs.json",
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": jobs,
            "relevant_items": relevant,
            "test_mode": is_test_mode(),
        },
    )
    print(f"Scored {len(jobs)} jobs; kept {len(relevant)} above threshold")


if __name__ == "__main__":
    main()
