#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ingest.gmail_imap_bridge as gmail_imap_bridge


def test_parser_name_for_route_uses_label_first_routing() -> None:
    assert gmail_imap_bridge.parser_name_for_route({"gmail_label": "pivot", "target": "grant_opportunities"}) == "grant_opportunities"
    assert gmail_imap_bridge.parser_name_for_route({"gmail_label": "grants", "target": "grant_opportunities"}) == "grant_opportunities"
    assert gmail_imap_bridge.parser_name_for_route({"gmail_label": "journals", "target": "journal_articles"}) == "journal_articles"
    assert gmail_imap_bridge.parser_name_for_route({"gmail_label": "news", "target": "news_items"}) == "news_items"
    assert gmail_imap_bridge.parser_name_for_route({"gmail_label": "misc", "target": "unknown"}) == "fallback_skip"


def test_route_description_mentions_backup_filters_without_making_them_primary() -> None:
    description = gmail_imap_bridge.route_description(
        {
            "gmail_label": "pivot",
            "target": "grant_opportunities",
            "from_contains": ["pivot"],
            "subject_contains": ["funding"],
        }
    )

    assert "label='pivot'" in description
    assert "parser=grant_opportunities" in description
    assert "backup filters=from_contains,subject_contains" in description


def test_main_writes_empty_snapshot_and_warning_when_imap_connection_fails(monkeypatch, tmp_path) -> None:
    output_path = tmp_path / "email_messages.json"
    state_path = tmp_path / "email_seen_messages.json"
    config_path = tmp_path / "email_ingest.yaml"
    config_path.write_text("email_ingest: {}\n", encoding="utf-8")

    monkeypatch.setattr(gmail_imap_bridge, "OUTPUT_PATH", output_path)
    monkeypatch.setattr(gmail_imap_bridge, "STATE_PATH", state_path)
    monkeypatch.setattr(gmail_imap_bridge, "CONFIG_PATH", config_path)
    monkeypatch.setattr(gmail_imap_bridge, "is_test_mode", lambda: False)
    monkeypatch.setattr(
        gmail_imap_bridge,
        "load_email_ingest_config",
        lambda _path: {
            "enabled": True,
            "provider": "gmail_imap",
            "host": "imap.gmail.com",
            "port": 993,
            "username_env": "JUNKYARD_GMAIL_USERNAME",
            "password_env": "JUNKYARD_GMAIL_APP_PASSWORD",
            "lookback_days": 14,
            "routes": [{"name": "pivot", "gmail_label": "pivot", "mailbox": "pivot", "target": "grant_opportunities"}],
        },
    )
    monkeypatch.setattr(gmail_imap_bridge, "load_imap_credentials", lambda _config: ("user@example.org", "app-password"))
    monkeypatch.setattr(gmail_imap_bridge, "connect_imap", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("imap down")))
    monkeypatch.setattr(gmail_imap_bridge, "load_seen_message_keys", lambda: set())
    monkeypatch.setattr(gmail_imap_bridge, "save_seen_message_keys", lambda _keys: None)

    gmail_imap_bridge.main()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["items"] == []
    assert payload["route_counts"] == {}
    assert payload["warnings"]
    assert "imap down" in payload["warnings"][0]
