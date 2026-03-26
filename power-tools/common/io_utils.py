#!/usr/bin/env python3
"""Shared file helpers for the power-tools pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = ROOT / "configs"
DATA_DIR = ROOT / "data"
INGEST_DIR = DATA_DIR / "ingest"
PROCESSING_DIR = DATA_DIR / "processing"
OUTPUT_DIR = DATA_DIR / "output"
STATE_DIR = DATA_DIR / "state"


def ensure_data_dirs() -> None:
    for path in (INGEST_DIR, PROCESSING_DIR, OUTPUT_DIR, STATE_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_yaml(path: Path) -> dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required. Install it with 'pip install pyyaml'.")
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML file must contain a top-level mapping: {path}")
    return data


def load_email_ingest_config(path: Path | None = None) -> dict[str, Any]:
    """Load email ingest config and normalize legacy/new shapes."""
    config_path = path or (CONFIGS_DIR / "email_ingest.yaml")
    raw = load_yaml(config_path)

    email_cfg = raw.get("email_ingest", raw)
    routing_cfg = raw.get("routing", {})
    imap_cfg = raw.get("imap", {})

    labels = [str(label).strip() for label in email_cfg.get("labels", []) if str(label).strip()]
    label_map = routing_cfg.get("email_label_map", {}) or {}
    default_lookback = int(email_cfg.get("lookback_days", raw.get("lookback_days", 7)))
    default_max_messages = int(email_cfg.get("max_messages_per_label", raw.get("max_messages_per_label", 25)))
    unread_only = bool(email_cfg.get("unread_only", False))

    routes = raw.get("routes")
    if routes is None:
        routes = []
        for label in labels:
            routes.append(
                {
                    "name": label,
                    "mailbox": label,
                    "gmail_label": label,
                    "target": label_map.get(label, ""),
                    "tags": ["email", label],
                    "lookback_days": default_lookback,
                    "max_messages": default_max_messages,
                    "include_seen": not unread_only,
                }
            )

    return {
        "enabled": bool(email_cfg.get("enabled", raw.get("enabled", True))),
        "provider": str(email_cfg.get("provider", raw.get("provider", "gmail_imap"))),
        "host": str(email_cfg.get("host", imap_cfg.get("host", "imap.gmail.com"))),
        "port": int(email_cfg.get("port", imap_cfg.get("port", 993))),
        "username_env": str(email_cfg.get("username_env", imap_cfg.get("username_env", "GMAIL_IMAP_USERNAME"))),
        "password_env": str(email_cfg.get("password_env", imap_cfg.get("password_env", "GMAIL_IMAP_PASSWORD"))),
        "lookback_days": default_lookback,
        "max_messages_per_label": default_max_messages,
        "unread_only": unread_only,
        "labels": labels,
        "routing": {"email_label_map": label_map},
        "routes": routes,
    }


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)
