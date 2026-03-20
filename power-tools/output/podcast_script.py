#!/usr/bin/env python3
"""Generate a podcast-style script from the daily digest."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, OUTPUT_DIR, load_json, load_yaml
from common.llm import chat_completion
from common.runtime import is_test_mode


def main() -> None:
    output_cfg = load_yaml(CONFIGS_DIR / "output.yaml")
    digest = load_json(OUTPUT_DIR / "daily_digest.json", default={})
    if not digest:
        raise SystemExit("No digest found. Run processing/daily_digest.py first.")

    messages = [
        {
            "role": "system",
            "content": (
                "You are writing a concise podcast script for a daily lab briefing. "
                "Cover the strongest papers, the most relevant grants, and the most urgent project tasks. "
                "Keep it clear, warm, and practical."
            ),
        },
        {"role": "user", "content": digest["markdown"]},
    ]
    if is_test_mode():
        script = (
            f"# Sample Podcast Script for {digest.get('date', 'unknown')}\n\n"
            "Welcome to the sample daily lab briefing.\n\n"
            "Today we have preview content generated in test mode, so nothing external was published or updated.\n\n"
            "Use this run to verify formatting, downstream Matrix delivery, and artifact generation."
        )
    else:
        script = chat_completion(messages, max_tokens=1800, temperature=0.4)
    script_file = output_cfg.get("podcast", {}).get("script_file", "podcast_script.md")
    (OUTPUT_DIR / script_file).write_text(script, encoding="utf-8")
    print(f"Wrote podcast script to {OUTPUT_DIR / script_file}")


if __name__ == "__main__":
    main()
