#!/usr/bin/env python3
"""Fetch routed Gmail/IMAP messages into a local JSON snapshot."""

from __future__ import annotations

import datetime
import email
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.email_source_registry import parser_name_for_route
from common.email_utils import connect_imap, load_imap_credentials, normalize_message_record, route_matches, search_uids
from common.io_utils import CONFIGS_DIR, INGEST_DIR, STATE_DIR, dump_json, ensure_data_dirs, load_email_ingest_config, load_json
from common.runtime import is_test_mode


CONFIG_PATH = CONFIGS_DIR / "email_ingest.yaml"
STATE_PATH = STATE_DIR / "email_seen_messages.json"
OUTPUT_PATH = INGEST_DIR / "email_messages.json"


def sample_items() -> list[dict]:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return [
        {
            "source_type": "email_imap",
            "route_name": "pivot",
            "target": "grant_opportunities",
            "mailbox": "pivot",
            "gmail_label": "pivot",
            "message_id": "<sample-pivot-rp@example.org>",
            "message_key": "pivot::<sample-pivot-rp@example.org>",
            "imap_uid": "101",
            "subject": "New funding call: Urban wetlands adaptation catalyst grant",
            "from": "Pivot-RP Alerts <alerts@example.org>",
            "published": now,
            "summary": "New funding call relevant to restoration, adaptation, and urban wetlands partnerships.",
            "body_text": "Funding alert with program summary and a link to the full call.",
            "body_html_text": "Funding alert with program summary and a link to the full call.",
            "links": ["https://example.org/grants/urban-wetlands-catalyst"],
            "link": "https://example.org/grants/urban-wetlands-catalyst",
            "tags": ["email", "pivot", "test-mode"],
        },
        {
            "source_type": "email_imap",
            "route_name": "journals",
            "target": "journal_articles",
            "mailbox": "journals",
            "gmail_label": "journals",
            "message_id": "<sample-journal@example.org>",
            "message_key": "journals::<sample-journal@example.org>",
            "imap_uid": "102",
            "subject": "New issue alert: Restoration Ecology",
            "from": "Wiley Journal Alerts <alerts@example.org>",
            "published": now,
            "summary": "Table of contents email covering new restoration and conservation articles.",
            "body_text": "New issue alert with article list and one highlighted paper link.",
            "body_html_text": "New issue alert with article list and one highlighted paper link.",
            "links": ["https://example.org/journals/restoration-ecology/new-issue"],
            "link": "https://example.org/journals/restoration-ecology/new-issue",
            "tags": ["email", "journals", "test-mode"],
        },
        {
            "source_type": "email_imap",
            "route_name": "news",
            "target": "news_items",
            "mailbox": "news",
            "gmail_label": "news",
            "message_id": "<sample-news@example.org>",
            "message_key": "news::<sample-news@example.org>",
            "imap_uid": "103",
            "subject": "Research news: Indigenous-led wetland restoration agreement announced",
            "from": "Research News Alerts <alerts@example.org>",
            "published": now,
            "summary": "News alert covering a restoration partnership and policy-relevant conservation funding signals.",
            "body_text": "Policy and restoration news alert with a source article link.",
            "body_html_text": "Policy and restoration news alert with a source article link.",
            "links": ["https://example.org/news/indigenous-led-wetland-restoration"],
            "link": "https://example.org/news/indigenous-led-wetland-restoration",
            "tags": ["email", "news", "test-mode"],
        },
    ]
def route_description(route: dict) -> str:
    label = str(route.get("gmail_label") or route.get("mailbox") or route.get("name") or "unknown").strip()
    parser_name = parser_name_for_route(route)
    heuristics = []
    if route.get("from_contains"):
        heuristics.append("from_contains")
    if route.get("subject_contains"):
        heuristics.append("subject_contains")
    heuristic_text = f"; backup filters={','.join(heuristics)}" if heuristics else ""
    return f"label={label!r} -> parser={parser_name}{heuristic_text}"


def route_count_summary(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        route_name = str(item.get("route_name", "unknown")).strip() or "unknown"
        counts[route_name] = counts.get(route_name, 0) + 1
    return counts


def load_seen_message_keys() -> set[str]:
    state = load_json(STATE_PATH, default={}) or {}
    return set(state.get("seen_message_keys", []))


def save_seen_message_keys(keys: set[str]) -> None:
    dump_json(
        STATE_PATH,
        {
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "seen_message_keys": sorted(keys),
        },
    )


def fetch_route_messages(client, route: dict, seen_message_keys: set[str], default_lookback_days: int) -> list[dict]:
    mailbox = str(route.get("mailbox") or route.get("gmail_label") or "").strip()
    if not mailbox:
        print("[gmail_imap_bridge] WARNING: route missing mailbox/gmail_label; skipping", file=sys.stderr)
        return []

    status, _data = client.select(mailbox, readonly=True)
    if status != "OK":
        print(f"[gmail_imap_bridge] WARNING: unable to select mailbox {mailbox!r}; skipping", file=sys.stderr)
        return []

    lookback_days = int(route.get("lookback_days", default_lookback_days))
    include_seen = bool(route.get("include_seen", False))
    max_messages = int(route.get("max_messages", 25))
    records: list[dict] = []
    matched = 0
    filtered_by_heuristics = 0

    for uid in reversed(search_uids(client, include_seen=include_seen, lookback_days=lookback_days)):
        status, data = client.uid("fetch", uid, "(RFC822)")
        if status != "OK" or not data or not data[0]:
            print(f"[gmail_imap_bridge] WARNING: fetch failed for mailbox {mailbox!r} uid {uid!r}", file=sys.stderr)
            continue
        raw_message = data[0][1]
        message = email.message_from_bytes(raw_message)
        record = normalize_message_record(message, route, mailbox=mailbox, uid=uid)
        if record["message_key"] in seen_message_keys:
            continue
        if not route_matches(record, route):
            filtered_by_heuristics += 1
            continue
        records.append(record)
        seen_message_keys.add(record["message_key"])
        matched += 1
        if len(records) >= max_messages:
            break

    print(
        f"[gmail_imap_bridge] selected {route_description(route)}; matched={matched}, filtered_by_backup_heuristics={filtered_by_heuristics}"
    )
    return records


def main() -> None:
    ensure_data_dirs()
    if is_test_mode():
        items = sample_items()
        counts = route_count_summary(items)
        sample_routes = [
            {"name": "pivot", "gmail_label": "pivot", "target": "grant_opportunities"},
            {"name": "journals", "gmail_label": "journals", "target": "journal_articles"},
            {"name": "news", "gmail_label": "news", "target": "news_items"},
        ]
        for route in sample_routes:
            print(f"[gmail_imap_bridge] routing {route_description(route)}")
        dump_json(
            OUTPUT_PATH,
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": items,
                "route_counts": counts,
                "test_mode": True,
            },
        )
        print(f"Wrote {len(items)} sample email records to {OUTPUT_PATH}")
        if counts:
            print("[gmail_imap_bridge] route counts: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
        return

    if not CONFIG_PATH.exists():
        dump_json(
            OUTPUT_PATH,
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": [],
                "warnings": [f"Config not found: {CONFIG_PATH.name}"],
            },
        )
        print(f"[gmail_imap_bridge] No config found at {CONFIG_PATH}; wrote empty snapshot")
        return

    items: list[dict] = []
    warnings: list[str] = []
    seen_message_keys = load_seen_message_keys()

    try:
        config = load_email_ingest_config(CONFIG_PATH)
        if not config.get("enabled", True):
            dump_json(
                OUTPUT_PATH,
                {
                    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "items": [],
                    "warnings": ["Email ingestion disabled in config"],
                },
            )
            print("[gmail_imap_bridge] Email ingestion disabled")
            return

        provider = str(config.get("provider", "gmail_imap"))
        if provider != "gmail_imap":
            raise RuntimeError(f"Unsupported email_ingest.provider: {provider}")

        username, password = load_imap_credentials(config)
        host = str(config.get("host", "imap.gmail.com"))
        port = int(config.get("port", 993))
        default_lookback_days = int(config.get("lookback_days", 7))

        routes = config.get("routes", [])
        if not routes:
            dump_json(
                OUTPUT_PATH,
                {
                    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "items": [],
                    "warnings": ["No routes configured"],
                },
            )
            print("[gmail_imap_bridge] No routes configured; wrote empty snapshot")
            return

        for route in routes:
            if not isinstance(route, dict):
                warnings.append("Route entry is not a mapping; skipped")
                continue
            print(f"[gmail_imap_bridge] routing {route_description(route)}")
            client = None
            try:
                client = connect_imap(host=host, port=port, username=username, password=password)
                items.extend(fetch_route_messages(client, route, seen_message_keys, default_lookback_days))
            except Exception as exc:
                route_name = route.get("name", route.get("mailbox", "unknown route"))
                warning = f"{route_name}: {exc}"
                warnings.append(warning)
                print(f"[gmail_imap_bridge] WARNING: {warning}", file=sys.stderr)
                continue
            finally:
                if client is not None:
                    try:
                        client.logout()
                    except Exception:
                        pass
    except Exception as exc:
        warning = f"email ingestion unavailable: {exc}"
        warnings.append(warning)
        print(f"[gmail_imap_bridge] WARNING: {warning}", file=sys.stderr)

    save_seen_message_keys(seen_message_keys)
    counts = route_count_summary(items)
    dump_json(
        OUTPUT_PATH,
        {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "items": items,
            "route_counts": counts,
            "warnings": warnings,
        },
    )
    print(f"Wrote {len(items)} email records to {OUTPUT_PATH}")
    if counts:
        print("[gmail_imap_bridge] route counts: " + ", ".join(f"{name}={count}" for name, count in sorted(counts.items())))
    if warnings:
        print(f"[gmail_imap_bridge] warnings: {len(warnings)}", file=sys.stderr)


if __name__ == "__main__":
    main()
