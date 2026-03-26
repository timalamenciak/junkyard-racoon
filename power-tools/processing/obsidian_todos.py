#!/usr/bin/env python3
"""Extract tasks from Obsidian project files and prioritize them with the LLM."""

from __future__ import annotations

import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, PROCESSING_DIR, dump_json, ensure_data_dirs, load_yaml
from common.llm import chat_completion, extract_json_payload
from common.runtime import is_test_mode


MAX_NOTE_CHARS = 6000
BATCH_SIZE = 10


def sample_items() -> list[dict]:
    return [
        {
            "vault": "sample-vault",
            "note": "Projects/Restoration Sprint.md",
            "project": "Restoration Sprint",
            "priority": "high",
            "impact": "high",
            "effort": "low",
            "task": "Confirm the shortlist of papers for journal club.",
            "owner_guess": "Tim",
            "deadline_guess": "This week",
            "rationale": "High impact and low effort because it unblocks a near-term discussion.",
        },
        {
            "vault": "sample-vault",
            "note": "Projects/Grant Tracker.md",
            "project": "Grant Tracker",
            "priority": "medium",
            "impact": "medium",
            "effort": "medium",
            "task": "Check eligibility notes for the biodiversity catalyst grant.",
            "owner_guess": "Lab admin",
            "deadline_guess": "Before next digest run",
            "rationale": "Useful for previewing digest formatting without scanning a vault.",
        },
    ]


def discover_project_notes(vault_paths: list[Path], project_globs: list[str]) -> list[dict]:
    note_payloads: list[dict] = []
    for vault in vault_paths:
        if not vault.exists():
            note_payloads.append({"vault": str(vault), "error": "missing_vault"})
            continue

        if project_globs:
            matched_notes: set[Path] = set()
            for pattern in project_globs:
                matched_notes.update(path for path in vault.glob(pattern) if path.is_file())
            notes = sorted(matched_notes)
        else:
            notes = sorted(vault.rglob("*.md"))

        for note in notes:
            text = note.read_text(encoding="utf-8", errors="replace")
            try:
                note_name = str(note.relative_to(vault))
            except ValueError:
                note_name = str(note)
            note_payloads.append(
                {
                    "vault": str(vault),
                    "note": note_name,
                    "project": note.stem,
                    "content": text[:MAX_NOTE_CHARS],
                }
            )
    return note_payloads


def extract_tasks_from_notes(source_notes: list[dict], extraction_rules: list[str]) -> list[dict]:
    extracted_items: list[dict] = []
    system = (
        "You are reviewing Obsidian project files for a research lab.\n"
        "Read each project file and convert it into a list of concrete next tasks.\n"
        "Return only JSON as a list of objects with keys: index, tasks.\n"
        "Each tasks value must be a list of objects with keys: task, owner_guess, deadline_guess, rationale.\n"
        "Only include concrete, actionable tasks."
    )
    rules_block = "\n".join(f"- {item}" for item in extraction_rules)

    for batch_start in range(0, len(source_notes), BATCH_SIZE):
        batch_notes = source_notes[batch_start : batch_start + BATCH_SIZE]
        batch_prompts = [
            f"[{i}] Project file: {note['note']}\nProject: {note['project']}\nContent:\n{note['content']}"
            for i, note in enumerate(batch_notes)
        ]
        user = f"Extraction rules:\n{rules_block}\n\nProject files:\n\n" + "\n\n".join(batch_prompts)
        try:
            response = chat_completion(
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=3200,
                temperature=0.0,
            )
            parsed = extract_json_payload(response)
        except Exception as exc:
            print(
                f"Warning: LLM task extraction failed for files {batch_start}-{batch_start + len(batch_notes) - 1}: {exc}",
                file=sys.stderr,
            )
            continue

        for item in parsed:
            local_idx = item.get("index")
            if not (isinstance(local_idx, int) and 0 <= local_idx < len(batch_notes)):
                continue
            note = batch_notes[local_idx]
            for task in item.get("tasks", []):
                task_text = str(task.get("task", "")).strip()
                if not task_text:
                    continue
                extracted_items.append(
                    {
                        "vault": note.get("vault", ""),
                        "note": note.get("note", ""),
                        "project": note.get("project", ""),
                        "task": task_text,
                        "owner_guess": str(task.get("owner_guess", "")).strip(),
                        "deadline_guess": str(task.get("deadline_guess", "")).strip(),
                        "rationale": str(task.get("rationale", "")).strip(),
                    }
                )

    return extracted_items


def prioritize_tasks(extracted_items: list[dict]) -> list[dict]:
    if not extracted_items:
        return []

    task_lines = []
    for idx, item in enumerate(extracted_items):
        task_lines.append(
            f"[{idx}] Project: {item.get('project', '')}\n"
            f"Task: {item.get('task', '')}\n"
            f"Owner guess: {item.get('owner_guess', '')}\n"
            f"Deadline cue: {item.get('deadline_guess', '')}\n"
            f"Context: {item.get('rationale', '')}\n"
            f"Source file: {item.get('note', '')}"
        )

    system = (
        "You are prioritizing a research lab task list.\n"
        "Prioritize highest-impact, lowest-effort work first, while still promoting urgent deadline-driven items.\n"
        "Return only JSON as a list of objects with keys: index, priority, impact, effort, rationale.\n"
        "Priority must be urgent, high, medium, or low.\n"
        "Impact and effort must be high, medium, or low."
    )
    user = "Prioritize these extracted project tasks:\n\n" + "\n\n".join(task_lines)
    response = chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=3200,
        temperature=0.0,
    )
    parsed = extract_json_payload(response)

    prioritized_items: list[dict] = []
    for item in parsed:
        source_idx = item.get("index")
        if not (isinstance(source_idx, int) and 0 <= source_idx < len(extracted_items)):
            continue
        source = extracted_items[source_idx]
        prioritized_items.append(
            {
                **source,
                "priority": item.get("priority", "medium"),
                "impact": item.get("impact", "medium"),
                "effort": item.get("effort", "medium"),
                "rationale": str(item.get("rationale", source.get("rationale", ""))).strip(),
            }
        )
    return prioritized_items


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
                "tasks_extracted": len(items),
                "items": items,
                "test_mode": True,
            },
        )
        print(f"Extracted {len(items)} sample Obsidian tasks")
        return

    vault_paths = [Path(path) for path in config.get("obsidian_vault_paths", [])]
    project_globs = list(config.get("todo_project_globs", ["Projects/**/*.md"]))
    note_payloads = discover_project_notes(vault_paths, project_globs)
    source_notes = [note for note in note_payloads if not note.get("error")]

    extracted_items: list[dict] = []
    prioritized_items: list[dict] = []
    if source_notes:
        extracted_items = extract_tasks_from_notes(source_notes, config.get("todo_extraction_rules", []))
        try:
            prioritized_items = prioritize_tasks(extracted_items)
        except Exception as exc:
            print(f"Warning: LLM task prioritization failed: {exc}", file=sys.stderr)
            prioritized_items = extracted_items

    dump_json(
        PROCESSING_DIR / "obsidian_todos.json",
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "notes_scanned": len(source_notes),
            "tasks_extracted": len(extracted_items),
            "items": prioritized_items,
        },
    )
    print(f"Extracted {len(extracted_items)} tasks from {len(source_notes)} project files")
    print(f"Prioritized {len(prioritized_items)} tasks")


if __name__ == "__main__":
    main()
