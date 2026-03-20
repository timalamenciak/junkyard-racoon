#!/usr/bin/env python3
"""Extract prioritized tasks from Obsidian project notes using the configured LLM."""

from __future__ import annotations

import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_yaml
from common.llm import chat_completion, extract_json_payload
from common.runtime import is_test_mode


MAX_NOTE_CHARS = 6000


def sample_items() -> list[dict]:
    return [
        {
            "vault": "sample-vault",
            "note": "Projects/Restoration Sprint.md",
            "priority": "high",
            "task": "Confirm the shortlist of papers for journal club.",
            "owner_guess": "Tim",
            "deadline_guess": "This week",
            "rationale": "Sample task generated in test mode.",
        },
        {
            "vault": "sample-vault",
            "note": "Projects/Grant Tracker.md",
            "priority": "medium",
            "task": "Check eligibility notes for the biodiversity catalyst grant.",
            "owner_guess": "Lab admin",
            "deadline_guess": "Before next digest run",
            "rationale": "Useful for previewing digest formatting without scanning a vault.",
        },
    ]


def main() -> None:
    ensure_data_dirs()
    config = load_yaml(CONFIGS_DIR / "lab_profile.yaml")
    if is_test_mode():
        items = sample_items()
        dump_json(
            PROCESSING_DIR / "obsidian_todos.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "notes_scanned": 0,
                "items": items,
                "test_mode": True,
            },
        )
        print(f"Extracted {len(items)} sample Obsidian tasks")
        return

    vault_paths = [Path(path) for path in config.get("obsidian_vault_paths", [])]
    note_payloads: list[dict] = []

    for vault in vault_paths:
        if not vault.exists():
            note_payloads.append({"vault": str(vault), "error": "missing_vault"})
            continue
        for note in vault.rglob("*.md"):
            text = note.read_text(encoding="utf-8", errors="replace")
            note_payloads.append(
                {
                    "vault": str(vault),
                    "note": str(note),
                    "content": text[:MAX_NOTE_CHARS],
                }
            )

    prompt_notes = []
    source_notes = []
    for note in note_payloads:
        if note.get("error"):
            continue
        source_notes.append(note)
    for idx, note in enumerate(source_notes):
        prompt_notes.append(f"[{idx}] Note: {note['note']}\nContent:\n{note['content']}")

    extracted_items: list[dict] = []
    if prompt_notes:
        extraction_rules = "\n".join(f"- {item}" for item in config.get("todo_extraction_rules", []))
        system = (
            "You are reviewing Obsidian project notes for a research lab.\n"
            "Extract concrete tasks, deadlines, and follow-ups for a daily lab operations briefing.\n"
            "Return only JSON as a list of objects with keys: index, priority, task, owner_guess, deadline_guess, rationale.\n"
            "Priority must be urgent, high, medium, or low."
        )
        user = f"Extraction rules:\n{extraction_rules}\n\nNotes:\n\n" + "\n\n".join(prompt_notes)
        response = chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=3200,
            temperature=0.0,
        )
        parsed = extract_json_payload(response)
        for item in parsed:
            idx = item.get("index")
            if isinstance(idx, int) and 0 <= idx < len(source_notes):
                extracted_items.append(
                    {
                        "vault": source_notes[idx].get("vault", ""),
                        "note": source_notes[idx].get("note", ""),
                        "priority": item.get("priority", "medium"),
                        "task": item.get("task", ""),
                        "owner_guess": item.get("owner_guess", ""),
                        "deadline_guess": item.get("deadline_guess", ""),
                        "rationale": item.get("rationale", ""),
                    }
                )

    dump_json(
        PROCESSING_DIR / "obsidian_todos.json",
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "notes_scanned": len(source_notes),
            "items": extracted_items,
        },
    )
    print(f"Extracted {len(extracted_items)} prioritized Obsidian tasks")


if __name__ == "__main__":
    main()
