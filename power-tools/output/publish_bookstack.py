#!/usr/bin/env python3
"""Publish digest sections to separate BookStack pages."""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, OUTPUT_DIR, load_json, load_yaml
from common.io_utils import INGEST_DIR, STATE_DIR, dump_json
from common.runtime import is_test_mode


def auth_headers(config: dict) -> dict[str, str]:
    return {
        "Authorization": f"Token {config['token_id']}:{config['token_secret']}",
        "Content-Type": "application/json",
    }


def put_page(base_url: str, page_id: int, name: str, markdown: str, hdrs: dict[str, str]) -> dict:
    req = urllib.request.Request(
        f"{base_url}/api/pages/{page_id}",
        data=json.dumps({"name": name, "markdown": markdown}).encode("utf-8"),
        headers=hdrs,
        method="PUT",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def build_articles_markdown(digest: dict) -> str:
    date_str = digest.get("date", "unknown")
    lines = [f"# Journal Articles — {date_str}", ""]
    articles = digest.get("relevant_articles", [])
    if articles:
        for a in articles:
            lines.append(f"## {a.get('title', '')}")
            lines.append(f"**Relevance:** {int(a.get('relevance_score', 0) * 100)}%  ")
            lines.append(f"**Feed:** {a.get('feed', '')}  ")
            if a.get("llm_summary"):
                lines.append(f"\n{a['llm_summary']}")
            if a.get("rationale"):
                lines.append(f"\n*{a['rationale']}*")
            if a.get("recommended_action"):
                lines.append(f"\n**Recommended action:** {a['recommended_action']}")
            lines.append(f"\n{a.get('link', '')}")
            lines.append("")
    else:
        lines.append("No high-relevance articles today.")
        lines.append("")

    pubs = digest.get("collaborator_publications", [])
    lines.append("# Collaborator Publications")
    lines.append("")
    if pubs:
        for p in pubs:
            lines.append(f"- **{p.get('collaborator', '')}**: {p.get('title', '')}  ")
            lines.append(f"  {p.get('link', '')}")
    else:
        lines.append("No recent collaborator publications.")
    return "\n".join(lines) + "\n"


def build_grants_markdown(digest: dict) -> str:
    date_str = digest.get("date", "unknown")
    lines = [f"# Grant Opportunities — {date_str}", ""]
    grants = digest.get("relevant_grants", [])
    if grants:
        for g in grants:
            lines.append(f"## {g.get('title', '')}")
            lines.append(f"**Fit:** {int(g.get('relevance_score', 0) * 100)}%  ")
            lines.append(f"**Source:** {g.get('source', '')}  ")
            if g.get("llm_summary"):
                lines.append(f"\n{g['llm_summary']}")
            if g.get("rationale"):
                lines.append(f"\n*{g['rationale']}*")
            if g.get("next_step"):
                lines.append(f"\n**Next step:** {g['next_step']}")
            lines.append(f"\n{g.get('link', '')}")
            lines.append("")
    else:
        lines.append("No high-fit grant opportunities today.")
    return "\n".join(lines) + "\n"


def build_tasks_markdown(digest: dict) -> str:
    date_str = digest.get("date", "unknown")
    lines = [f"# Project Tasks — {date_str}", ""]
    todos = digest.get("prioritized_todos", [])
    if todos:
        for t in todos:
            priority = t.get("priority", "medium").upper()
            lines.append(f"## [{priority}] {t.get('task', '')}")
            if t.get("owner_guess"):
                lines.append(f"**Owner:** {t['owner_guess']}  ")
            if t.get("deadline_guess"):
                lines.append(f"**Deadline cue:** {t['deadline_guess']}  ")
            if t.get("rationale"):
                lines.append(f"\n{t['rationale']}")
            if t.get("note"):
                lines.append(f"\n{t['note']}")
            lines.append(f"\n*From: {t.get('note', t.get('vault', ''))}*")
            lines.append("")
    else:
        lines.append("No priority tasks extracted today.")
    return "\n".join(lines) + "\n"


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
            "updated_at": digest_timestamp(),
            "seen_article_keys": sorted(seen | pending),
            "pending_article_keys": [],
        },
    )


def digest_timestamp() -> str:
    import datetime

    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main() -> None:
    digest = load_json(OUTPUT_DIR / "daily_digest.json", default={})
    if not digest:
        raise SystemExit("No daily digest found. Run processing/daily_digest.py first.")

    output_cfg = load_yaml(CONFIGS_DIR / "output.yaml")
    bookstack = output_cfg.get("bookstack", {})

    page_ids = bookstack.get("pages", {})
    articles_page_id = page_ids.get("articles")
    grants_page_id = page_ids.get("grants")
    tasks_page_id = page_ids.get("tasks")

    missing = [k for k, v in [("articles", articles_page_id), ("grants", grants_page_id), ("tasks", tasks_page_id)] if not v]
    if missing:
        raise SystemExit(f"bookstack.pages.{{{', '.join(missing)}}} not set in output.yaml")

    date_str = digest.get("date", "unknown")
    base_url = bookstack["url"].rstrip("/")

    if is_test_mode():
        payload_out = {
            "articles_url": f"{base_url}/pages/{articles_page_id}",
            "grants_url": f"{base_url}/pages/{grants_page_id}",
            "tasks_url": f"{base_url}/pages/{tasks_page_id}",
            "test_mode": True,
        }
        (OUTPUT_DIR / "bookstack_publish.json").write_text(json.dumps(payload_out, indent=2), encoding="utf-8")
        for url in [payload_out["articles_url"], payload_out["grants_url"], payload_out["tasks_url"]]:
            print(url)
        return

    hdrs = auth_headers(bookstack)
    results = {}

    for label, page_id, name, markdown in [
        ("articles", articles_page_id, f"Journal Articles — {date_str}", build_articles_markdown(digest)),
        ("grants",   grants_page_id,   f"Grant Opportunities — {date_str}", build_grants_markdown(digest)),
        ("tasks",    tasks_page_id,    f"Project Tasks — {date_str}", build_tasks_markdown(digest)),
    ]:
        result = put_page(base_url, page_id, name, markdown, hdrs)
        url = f"{base_url}/books/{result.get('book_slug', '')}/pages/{result.get('slug', '')}"
        results[f"{label}_url"] = url
        print(f"{label}: {url}")

    results["test_mode"] = False
    (OUTPUT_DIR / "bookstack_publish.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    promote_pending_rss_state()


if __name__ == "__main__":
    main()
