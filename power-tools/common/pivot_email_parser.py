#!/usr/bin/env python3
"""Helpers for parsing Pivot-RP grant alert emails."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from common.email_utils import collapse_ws, extract_links, strip_html


NOISE_PATTERNS = (
    "view online",
    "unsubscribe",
    "manage your alerts",
    "privacy policy",
    "email preferences",
)
DATE_PATTERNS = [
    re.compile(
        r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
]
LABELED_FIELD_PATTERNS = {
    "deadline": re.compile(r"\b(?:deadline|due date|closing date)\s*:\s*(.+?)(?:\||$)", re.IGNORECASE),
    "sponsor": re.compile(r"\b(?:sponsor|funder|funding organization)\s*:\s*(.+?)(?:\||$)", re.IGNORECASE),
    "alert_context": re.compile(r"\b(?:search|alert|query)\s*:\s*(.+?)(?:\||$)", re.IGNORECASE),
}


def _is_noise_link(text: str, href: str) -> bool:
    haystack = f"{text} {href}".lower()
    return any(pattern in haystack for pattern in NOISE_PATTERNS)


def _extract_field(pattern: re.Pattern[str], text: str) -> str:
    match = pattern.search(text)
    if not match:
        return ""
    value = collapse_ws(match.group(1))
    return re.split(
        r"(?=\b(?:deadline|due date|closing date|sponsor|funder|funding organization|alert|search|query)\b\s*:)",
        value,
        maxsplit=1,
    )[0].strip(" |;-")


def _extract_labeled_line(lines: list[str], prefixes: tuple[str, ...]) -> str:
    for line in lines:
        lower = line.lower()
        for prefix in prefixes:
            if lower.startswith(prefix):
                return collapse_ws(line.split(":", 1)[1] if ":" in line else line[len(prefix) :]).strip(" |;-")
    return ""


def _extract_deadline(text: str) -> str:
    labeled = _extract_field(LABELED_FIELD_PATTERNS["deadline"], text)
    if labeled:
        return labeled
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match:
            return collapse_ws(match.group(0))
    return ""


def _extract_alert_context(subject: str, text: str) -> str:
    labeled = _extract_field(LABELED_FIELD_PATTERNS["alert_context"], text)
    if labeled:
        return labeled
    subject_clean = collapse_ws(subject)
    if "pivot" in subject_clean.lower() or "alert" in subject_clean.lower():
        return subject_clean
    return ""


def _extract_summary(text: str, title: str, sponsor: str, deadline: str) -> str:
    summary = collapse_ws(text)
    for token in (title, sponsor, deadline):
        if token:
            summary = summary.replace(token, " ")
    summary = re.sub(r"\b(?:sponsor|funder|deadline|due date|closing date|search|alert)\s*:\s*", " ", summary, flags=re.IGNORECASE)
    return collapse_ws(summary)[:1200]


def parse_pivot_email_opportunities(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract one or more grant opportunities from a Pivot-style email record."""
    html = message.get("body_html", "") or ""
    text = message.get("body_text", "") or message.get("body_html_text", "") or ""
    subject = message.get("subject", "") or ""
    soup = BeautifulSoup(html, "html.parser") if html else None

    candidates: list[dict[str, str]] = []
    seen_links: set[str] = set()

    if soup is not None:
        for anchor in soup.find_all("a", href=True):
            title = collapse_ws(anchor.get_text(" ", strip=True))
            href = collapse_ws(anchor.get("href", ""))
            if len(title) < 12 or not href or href in seen_links or _is_noise_link(title, href):
                continue
            container = anchor
            for parent in anchor.parents:
                if getattr(parent, "name", "") in {"tr", "table", "div", "li", "td"}:
                    parent_text = collapse_ws(parent.get_text(" ", strip=True))
                    if len(parent_text) >= len(title) + 20:
                        container = parent
                        break
            container_text = collapse_ws(container.get_text(" ", strip=True))
            lines = [collapse_ws(value) for value in getattr(container, "stripped_strings", []) if collapse_ws(value)]
            candidates.append({"title": title, "link": href, "text": container_text, "lines": lines})
            seen_links.add(href)

    if not candidates:
        links = extract_links(text, html)
        fallback_title = collapse_ws(subject) or "Untitled funding alert"
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
        title = candidate["title"]
        body_text = candidate["text"]
        lines = candidate.get("lines", [])
        sponsor = _extract_labeled_line(lines, ("sponsor:", "funder:", "funding organization:")) or _extract_field(
            LABELED_FIELD_PATTERNS["sponsor"], body_text
        )
        deadline = _extract_labeled_line(lines, ("deadline:", "due date:", "closing date:")) or _extract_deadline(body_text)
        alert_context = _extract_alert_context(subject, text)
        summary = _extract_summary(body_text, title, sponsor, deadline) or collapse_ws(text)[:1200]
        items.append(
            {
                "title": title,
                "link": candidate["link"],
                "summary": summary,
                "deadline": deadline,
                "sponsor": sponsor,
                "alert_context": alert_context,
                "parsing_confidence": 0.85 if candidate["link"] and title != collapse_ws(subject) else 0.45,
            }
        )

    return items
