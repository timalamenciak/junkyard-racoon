#!/usr/bin/env python3
"""Small registry for email-driven ingest targets and their label aliases."""

from __future__ import annotations

from typing import Any


EMAIL_SOURCE_REGISTRY = {
    "grant_opportunities": {
        "labels": {"pivot", "grants"},
        "target_aliases": {"grant_opportunities", "grants"},
        "parser_name": "grant_opportunities",
    },
    "journal_articles": {
        "labels": {"journals"},
        "target_aliases": {"journal_articles", "journals"},
        "parser_name": "journal_articles",
    },
    "news_items": {
        "labels": {"news"},
        "target_aliases": {"news_items"},
        "parser_name": "news_items",
    },
    "job_openings": {
        "labels": {"jobs"},
        "target_aliases": {"job_openings", "jobs"},
        "parser_name": "job_openings",
    },
}


def normalize_label(value: str | None) -> str:
    return str(value or "").strip().lower()


def normalize_target(value: str | None) -> str:
    return str(value or "").strip()


def parser_name_for_route(route: dict[str, Any]) -> str:
    target = normalize_target(route.get("target"))
    label = normalize_label(route.get("gmail_label") or route.get("mailbox") or route.get("name"))
    for target_name, config in EMAIL_SOURCE_REGISTRY.items():
        if target in config["target_aliases"] or label in config["labels"]:
            return str(config["parser_name"])
    return "fallback_skip"


def route_matches_target(message: dict[str, Any], target_name: str) -> bool:
    target = normalize_target(message.get("target"))
    config = EMAIL_SOURCE_REGISTRY.get(target_name, {})
    aliases = set(config.get("target_aliases", {target_name}))
    return target in aliases
