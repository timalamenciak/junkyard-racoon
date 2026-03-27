#!/usr/bin/env python3
"""Extract tasks from Obsidian project files and identify high-impact ones with the LLM."""

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


def extract_tasks_from_note(note: dict, extraction_rules: list[str]) -> list[dict]:
    """Send a single project note to the LLM and identify the next 3-5 tasks."""
    rules_block = "\n".join(f"- {r}" for r in extraction_rules)
    system = (
        "You are reviewing an Obsidian project file for a research lab.\n"
        "Identify the next 3-5 concrete, actionable tasks for this project.\n"
        "Return only JSON as a list of objects with keys: task, owner_guess, deadline_guess, rationale.\n"
        "Limit to 5 tasks maximum. Only include concrete, actionable next steps."
    )
    user = (
        f"Project: {note['project']}\n"
        f"File: {note['note']}\n\n"
        f"Extraction rules:\n{rules_block}\n\n"
        f"Project file content:\n{note['content']}\n\n"
        "Identify the next 3-5 tasks for this project."
    )
    try:
        response = chat_completion(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=1200,
            temperature=0.0,
        )
        parsed = extract_json_payload(response)
        if not isinstance(parsed, list):
            return []
        items = []
        for task in parsed:
            if not isinstance(task, dict):
                print(
                    f"Warning: skipping malformed task extraction item for {note.get('note', '?')}: {task!r}",
                    file=sys.stderr,
                )
                continue
            task_text = str(task.get("task", "")).strip()
            if not task_text:
                continue
            items.append(
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
        return items
    except Exception as exc:
        print(
            f"Warning: task extraction failed for {note.get('note', '?')}: {exc}",
            file=sys.stderr,
        )
        return []


def score_task_impact(extracted_items: list[dict]) -> list[dict]:
    """Send all collected tasks back to the LLM and ask which are high impact."""
    if not extracted_items:
        return []

    task_lines = []
    for idx, item in enumerate(extracted_items):
        task_lines.append(
            f"[{idx}] Project: {item.get('project', '')}\n"
            f"Task: {item.get('task', '')}\n"
            f"Owner: {item.get('owner_guess', '')}\n"
            f"Deadline cue: {item.get('deadline_guess', '')}\n"
            f"Context: {item.get('rationale', '')}"
        )

    system = (
        "You are a research lab prioritization assistant.\n"
        "Review this list of potential project tasks and identify which are high impact for the lab.\n"
        "Prioritize highest-impact, lowest-effort work first; also promote urgent deadline-driven items.\n"
        "Return only JSON as a list of objects with keys: index, priority, impact, effort, rationale.\n"
        "priority must be: urgent, high, medium, or low.\n"
        "impact and effort must be: high, medium, or low."
    )
    user = "Which of these tasks are high impact?\n\n" + "\n\n".join(task_lines)

    response = chat_completion(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        max_tokens=3200,
        temperature=0.0,
    )
    parsed = extract_json_payload(response)
    if not isinstance(parsed, list):
        print("Warning: task impact scoring returned non-list payload", file=sys.stderr)
        return extracted_items

    prioritized: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            print(f"Warning: skipping malformed task scoring item: {item!r}", file=sys.stderr)
            continue
        source_idx = item.get("index")
        if not (isinstance(source_idx, int) and 0 <= source_idx < len(extracted_items)):
            continue
        source = extracted_items[source_idx]
        prioritized.append(
            {
                **source,
                "priority": item.get("priority", "medium"),
                "impact": item.get("impact", "medium"),
                "effort": item.get("effort", "medium"),
                "rationale": str(item.get("rationale", source.get("rationale", ""))).strip(),
            }
        )
    return prioritized


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

    if not vault_paths:
        print("WARNING [obsidian_todos] No obsidian_vault_paths configured in lab_profile.yaml", file=sys.stderr)

    # Report existence of each configured vault before scanning
    for vp in vault_paths:
        if vp.exists():
            print(f"  vault OK: {vp}")
        else:
            print(
                f"  WARNING [obsidian_todos] vault NOT FOUND: {vp}\n"
                f"    → On Linux/Ubuntu the vault must be synced to a local path.\n"
                f"    → Update obsidian_vault_paths in configs/lab_profile.yaml to the server path.",
                file=sys.stderr,
            )

    note_payloads = discover_project_notes(vault_paths, project_globs)
    source_notes = [note for note in note_payloads if not note.get("error")]
    missing_vaults = [note for note in note_payloads if note.get("error") == "missing_vault"]

    if missing_vaults:
        print(
            f"WARNING [obsidian_todos] {len(missing_vaults)} vault(s) inaccessible — "
            f"0 notes scanned from those vaults.",
            file=sys.stderr,
        )

    if not source_notes:
        dump_json(
            PROCESSING_DIR / "obsidian_todos.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "notes_scanned": 0,
                "tasks_extracted": 0,
                "items": [],
                "vault_errors": [note["vault"] for note in missing_vaults],
                "diagnostic": (
                    "No notes scanned. Check that obsidian_vault_paths in lab_profile.yaml "
                    "points to a path accessible from this machine."
                ),
            },
        )
        print("WARNING [obsidian_todos] No project notes found to scan — tasks section will be empty in digest.", file=sys.stderr)
        return

    # Step 1: send each note individually to extract 3-5 tasks
    extraction_rules = config.get("todo_extraction_rules", [])
    extracted_items: list[dict] = []
    for note in source_notes:
        tasks = extract_tasks_from_note(note, extraction_rules)
        extracted_items.extend(tasks)
        if tasks:
            print(f"  {note['note']}: {len(tasks)} tasks extracted")

    # Step 2: send all collected tasks back and ask which are high impact
    prioritized_items: list[dict] = extracted_items
    if extracted_items:
        try:
            prioritized_items = score_task_impact(extracted_items)
        except Exception as exc:
            print(f"Warning: LLM impact scoring failed: {exc}", file=sys.stderr)
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
    print(f"Scored {len(prioritized_items)} tasks by impact")


if __name__ == "__main__":
    main()
