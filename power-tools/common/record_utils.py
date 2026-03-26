#!/usr/bin/env python3
"""Helpers for canonical URLs, cross-source fingerprints, and conservative merges."""

from __future__ import annotations

import re
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from common.email_utils import collapse_ws


TRACKING_QUERY_PREFIXES = ("utm_", "mc_", "mkt_", "fbclid", "gclid")
TITLE_PUNCT_RE = re.compile(r"[^a-z0-9]+")


def canonicalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    path = parsed.path or ""
    if path != "/":
        path = path.rstrip("/")
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key and not any(key.lower().startswith(prefix) for prefix in TRACKING_QUERY_PREFIXES)
    ]
    query = urlencode(query_pairs)
    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_title(title: str) -> str:
    text = TITLE_PUNCT_RE.sub(" ", collapse_ws(title).lower())
    return collapse_ws(text)


def normalize_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw or raw == "unknown":
        return ""
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except Exception:
        pass
    try:
        return raw[:10] if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-" else raw
    except Exception:
        return raw


def fingerprint_record(title: str, url: str, published: str) -> str:
    normalized_url = canonicalize_url(url)
    normalized_title = normalize_title(title)
    normalized_date = normalize_date(published)
    return "||".join((normalized_title, normalized_url, normalized_date))


def _merge_value(existing: Any, incoming: Any) -> Any:
    if existing in (None, "", [], {}):
        return incoming
    return existing


def merge_tags(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    for value in list(existing or []) + list(incoming or []):
        text = str(value).strip()
        if text and text not in merged:
            merged.append(text)
    return merged


def merge_provenance(existing: list[str], incoming: list[str]) -> list[str]:
    return merge_tags(existing, incoming)


def merge_records(existing: dict[str, Any], incoming: dict[str, Any], preserve_keys: tuple[str, ...] = ()) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in incoming.items():
        if key in preserve_keys:
            continue
        if key == "tags":
            merged[key] = merge_tags(list(merged.get(key, [])), list(value or []))
            continue
        if key == "provenance":
            merged[key] = merge_provenance(list(merged.get(key, [])), list(value or []))
            continue
        if key == "sources":
            merged[key] = merge_tags(list(merged.get(key, [])), list(value or []))
            continue
        merged[key] = _merge_value(merged.get(key), value)
    return merged
