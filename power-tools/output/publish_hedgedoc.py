#!/usr/bin/env python3
"""Publish rolling digest snapshots to HedgeDoc and archive them every two months."""

from __future__ import annotations

import calendar
import datetime
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, OUTPUT_DIR, STATE_DIR, dump_json, load_json, load_yaml
from common.runtime import is_test_mode


ROLLUP_STATE_PATH = STATE_DIR / "hedgedoc_rollups.json"


def auth_headers(config: dict, content_type: str) -> dict[str, str]:
    headers = {"Content-Type": content_type}
    token = config.get("api_token", "")
    cookie = config.get("session_cookie", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if cookie:
        headers["Cookie"] = cookie
    return headers


def publish_note(base_url: str, alias: str, markdown: str, hdrs: dict[str, str]) -> str:
    request = urllib.request.Request(
        f"{base_url}/new/{urllib.parse.quote(alias)}",
        data=markdown.encode("utf-8"),
        headers=hdrs,
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.geturl()


def bimonthly_period(target_date: datetime.date) -> dict[str, str]:
    start_month = target_date.month if target_date.month % 2 == 1 else target_date.month - 1
    end_month = start_month + 1
    start_date = datetime.date(target_date.year, start_month, 1)
    end_day = calendar.monthrange(target_date.year, end_month)[1]
    end_date = datetime.date(target_date.year, end_month, end_day)
    return {
        "key": f"{start_date:%Y-%m}_{end_date:%Y-%m}",
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
    }


def snapshot_alias(base_alias: str, date_str: str) -> str:
    return f"{base_alias}-rolling-{date_str}"


def archive_alias(base_alias: str, period_key: str) -> str:
    return f"{base_alias}-archive-{period_key}"


def build_articles_body(digest: dict) -> str:
    lines: list[str] = []
    articles = digest.get("relevant_articles", [])
    if articles:
        lines.append("### Relevant Papers")
        lines.append("")
        for article in articles:
            lines.append(f"- **{article.get('title', '')}** ({int(article.get('relevance_score', 0) * 100)}%)")
            lines.append(f"  Feed: {article.get('feed', '')}")
            if article.get("llm_summary"):
                lines.append(f"  {article['llm_summary']}")
            if article.get("recommended_action"):
                lines.append(f"  Recommended action: {article['recommended_action']}")
            lines.append(f"  {article.get('link', '')}")
        lines.append("")
    else:
        lines.append("### Relevant Papers")
        lines.append("")
        lines.append("- No high-relevance articles today.")
        lines.append("")

    publications = digest.get("collaborator_publications", [])
    lines.append("### Collaborator Publications")
    lines.append("")
    if publications:
        for publication in publications:
            lines.append(f"- **{publication.get('collaborator', '')}**: {publication.get('title', '')}")
            lines.append(f"  {publication.get('link', '')}")
    else:
        lines.append("- No recent collaborator publications.")
    return "\n".join(lines).strip()


def build_grants_body(digest: dict) -> str:
    lines = ["### Grant Opportunities", ""]
    grants = digest.get("relevant_grants", [])
    if grants:
        for grant in grants:
            lines.append(f"- **{grant.get('title', '')}** ({int(grant.get('relevance_score', 0) * 100)}%)")
            lines.append(f"  Source: {grant.get('source', '')}")
            if grant.get("llm_summary"):
                lines.append(f"  {grant['llm_summary']}")
            if grant.get("next_step"):
                lines.append(f"  Next step: {grant['next_step']}")
            lines.append(f"  {grant.get('link', '')}")
    else:
        lines.append("- No high-fit grant opportunities today.")
    return "\n".join(lines).strip()


def build_tasks_body(digest: dict) -> str:
    lines = ["### Project Tasks", ""]
    todos = digest.get("prioritized_todos", [])
    if todos:
        for todo in todos:
            priority = todo.get("priority", "medium").upper()
            lines.append(f"- **[{priority}] {todo.get('task', '')}**")
            if todo.get("project"):
                lines.append(f"  Project: {todo['project']}")
            if todo.get("impact") or todo.get("effort"):
                lines.append(f"  Impact / effort: {todo.get('impact', 'unknown')} / {todo.get('effort', 'unknown')}")
            if todo.get("owner_guess"):
                lines.append(f"  Owner: {todo['owner_guess']}")
            if todo.get("deadline_guess"):
                lines.append(f"  Deadline cue: {todo['deadline_guess']}")
            if todo.get("rationale"):
                lines.append(f"  Why now: {todo['rationale']}")
            lines.append(f"  Source: {todo.get('note', todo.get('vault', ''))}")
    else:
        lines.append("- No priority tasks extracted today.")
    return "\n".join(lines).strip()


def build_daily_entry(date_str: str, body: str) -> str:
    return f"## {date_str}\n\n{body.strip()}"


def prepend_entry(existing_markdown: str, entry_markdown: str) -> str:
    if not existing_markdown.strip():
        return entry_markdown.strip()
    return f"{entry_markdown.strip()}\n\n{existing_markdown.strip()}"


def render_live_note(title: str, period_start: str, period_end: str, content: str) -> str:
    lines = [
        f"# {title}",
        "",
        f"_Rolling window: {period_start} to {period_end}_",
        "",
        content.strip(),
        "",
    ]
    return "\n".join(lines)


def render_archive_note(title: str, period_start: str, period_end: str, content: str) -> str:
    lines = [
        f"# {title} Archive",
        "",
        f"_Archived window: {period_start} to {period_end}_",
        "",
        content.strip(),
        "",
    ]
    return "\n".join(lines)


def default_section_state() -> dict[str, str]:
    return {"content": "", "latest_snapshot_alias": "", "latest_snapshot_url": ""}


def load_rollup_state(current_period: dict[str, str]) -> dict:
    state = load_json(ROLLUP_STATE_PATH, default={}) or {}
    if not state:
        return {
            "period_key": current_period["key"],
            "period_start": current_period["start"],
            "period_end": current_period["end"],
            "sections": {},
        }
    state.setdefault("sections", {})
    return state


def save_rollup_state(state: dict) -> None:
    dump_json(ROLLUP_STATE_PATH, state)


def ensure_section_state(state: dict, label: str) -> dict:
    section_state = state["sections"].get(label)
    if not isinstance(section_state, dict):
        section_state = default_section_state()
        state["sections"][label] = section_state
    for key, value in default_section_state().items():
        section_state.setdefault(key, value)
    return section_state


def promote_pending_rss_state() -> None:
    state_path = STATE_DIR / "rss_seen_articles.json"
    state = load_json(state_path, default={}) or {}
    seen = set(state.get("seen_article_keys", []))
    pending = set(state.get("pending_article_keys", []))
    if not pending:
        return
    dump_json(
        state_path,
        {
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "seen_article_keys": sorted(seen | pending),
            "pending_article_keys": [],
        },
    )


def main() -> None:
    digest = load_json(OUTPUT_DIR / "daily_digest.json", default={})
    if not digest:
        raise SystemExit("No daily digest found. Run processing/daily_digest.py first.")

    output_cfg = load_yaml(CONFIGS_DIR / "output.yaml")
    hedgedoc = output_cfg.get("hedgedoc", {})
    aliases = hedgedoc.get("notes", {})

    base_url = hedgedoc.get("url", "").rstrip("/")
    if not base_url:
        raise SystemExit("hedgedoc.url not set in output.yaml")

    required = {
        "articles": aliases.get("articles"),
        "grants": aliases.get("grants"),
        "tasks": aliases.get("tasks"),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise SystemExit(f"hedgedoc.notes.{{{', '.join(missing)}}} not set in output.yaml")

    date_str = digest.get("date", datetime.date.today().isoformat())
    current_date = datetime.date.fromisoformat(date_str)
    current_period = bimonthly_period(current_date)
    state = load_rollup_state(current_period)

    section_titles = {
        "articles": "Journal Articles",
        "grants": "Grant Opportunities",
        "tasks": "Project Tasks",
    }
    section_bodies = {
        "articles": build_articles_body(digest),
        "grants": build_grants_body(digest),
        "tasks": build_tasks_body(digest),
    }

    results: dict[str, str | bool] = {
        "period_key": current_period["key"],
    }
    archive_payloads: list[tuple[str, str, str]] = []
    if state.get("period_key") and state.get("period_key") != current_period["key"]:
        old_period_key = str(state.get("period_key", ""))
        old_start = str(state.get("period_start", ""))
        old_end = str(state.get("period_end", ""))
        for label, base_alias in required.items():
            section_state = ensure_section_state(state, label)
            existing_content = section_state.get("content", "").strip()
            if not existing_content:
                continue
            archive_payloads.append(
                (
                    label,
                    archive_alias(base_alias, old_period_key),
                    render_archive_note(section_titles[label], old_start, old_end, existing_content),
                )
            )

        state = {
            "period_key": current_period["key"],
            "period_start": current_period["start"],
            "period_end": current_period["end"],
            "sections": {},
        }

    state["period_key"] = current_period["key"]
    state["period_start"] = current_period["start"]
    state["period_end"] = current_period["end"]

    snapshot_payloads: list[tuple[str, str, str]] = []
    for label, base_alias in required.items():
        section_state = ensure_section_state(state, label)
        today_entry = build_daily_entry(date_str, section_bodies[label])
        section_state["content"] = prepend_entry(section_state.get("content", ""), today_entry)
        snapshot = render_live_note(
            section_titles[label],
            current_period["start"],
            current_period["end"],
            section_state["content"],
        )
        alias = snapshot_alias(base_alias, date_str)
        section_state["latest_snapshot_alias"] = alias
        snapshot_payloads.append((label, alias, snapshot))

    if is_test_mode():
        for label, alias, _markdown in archive_payloads:
            results[f"{label}_archive_url"] = f"{base_url}/{alias}"
        for label, alias, _markdown in snapshot_payloads:
            results[f"{label}_url"] = f"{base_url}/{alias}"
            results[f"{label}_alias"] = alias
        results["test_mode"] = True
        save_rollup_state(state)
        (OUTPUT_DIR / "hedgedoc_publish.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        for label in ["articles", "grants", "tasks"]:
            print(f"{label}: {results[f'{label}_url']}")
            archive_key = f"{label}_archive_url"
            if archive_key in results:
                print(f"{label} archive: {results[archive_key]}")
        return

    hdrs = auth_headers(hedgedoc, "text/markdown; charset=utf-8")
    for label, alias, markdown in archive_payloads:
        archive_url = publish_note(base_url, alias, markdown, hdrs)
        results[f"{label}_archive_url"] = archive_url
        print(f"{label} archive: {archive_url}")

    for label, alias, markdown in snapshot_payloads:
        url = publish_note(base_url, alias, markdown, hdrs)
        section_state = ensure_section_state(state, label)
        section_state["latest_snapshot_url"] = url
        results[f"{label}_url"] = url
        results[f"{label}_alias"] = alias
        print(f"{label}: {url}")

    results["test_mode"] = False
    save_rollup_state(state)
    (OUTPUT_DIR / "hedgedoc_publish.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    promote_pending_rss_state()


if __name__ == "__main__":
    main()
