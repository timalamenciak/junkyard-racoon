#!/usr/bin/env python3
"""Helpers for parsing generic news digest emails."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from common.email_utils import collapse_ws, extract_links, strip_html


NOISE_PATTERNS = (
    "unsubscribe",
    "manage preferences",
    "view in browser",
    "advertisement",
    "sponsored",
    "sign in",
)
DATE_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)


def _is_noise_link(text: str, href: str) -> bool:
    haystack = f"{text} {href}".lower()
    return any(pattern in haystack for pattern in NOISE_PATTERNS)


def parse_news_email_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    html = message.get("body_html", "") or ""
    text = message.get("body_text", "") or message.get("body_html_text", "") or ""
    subject = message.get("subject", "") or ""
    soup = BeautifulSoup(html, "html.parser") if html else None

    candidates: list[dict[str, Any]] = []
    seen_links: set[str] = set()

    if soup is not None:
        for anchor in soup.find_all("a", href=True):
            title = collapse_ws(anchor.get_text(" ", strip=True))
            href = collapse_ws(anchor.get("href", ""))
            if len(title) < 12 or not href or href in seen_links or _is_noise_link(title, href):
                continue
            container = anchor
            for parent in anchor.parents:
                if getattr(parent, "name", "") in {"tr", "table", "div", "li", "td", "article"}:
                    parent_text = collapse_ws(parent.get_text(" ", strip=True))
                    if len(parent_text) >= len(title) + 20:
                        container = parent
                        break
            lines = [collapse_ws(value) for value in getattr(container, "stripped_strings", []) if collapse_ws(value)]
            candidates.append({"title": title, "link": href, "text": collapse_ws(container.get_text(" ", strip=True)), "lines": lines})
            seen_links.add(href)

    if not candidates:
        links = extract_links(text, html)
        fallback_title = collapse_ws(subject) or "Untitled news item"
        if links or text:
            candidates.append(
                {
                    "title": fallback_title,
                    "link": links[0] if links else "",
                    "text": text or strip_html(html),
                    "lines": [collapse_ws(text)] if text else [],
                }
            )

    items: list[dict[str, Any]] = []
    for candidate in candidates:
        published_hint = ""
        match = DATE_RE.search(candidate["text"])
        if match:
            published_hint = collapse_ws(match.group(0))
        summary = candidate["text"].replace(candidate["title"], " ")
        summary = collapse_ws(summary)[:1200]
        items.append(
            {
                "title": candidate["title"],
                "link": candidate["link"],
                "summary": summary or collapse_ws(text)[:1200],
                "published_hint": published_hint,
                "parsing_confidence": 0.75 if candidate["link"] and candidate["title"] != collapse_ws(subject) else 0.4,
            }
        )
    return items
