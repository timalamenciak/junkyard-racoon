#!/usr/bin/env python3
"""Helpers for parsing journal newsletter emails into article-like records."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from common.email_utils import collapse_ws, extract_links, strip_html


NOISE_PATTERNS = (
    "unsubscribe",
    "manage alerts",
    "view this email",
    "email preferences",
    "about this journal",
    "submit an article",
    "sign in",
)
JOURNAL_PREFIXES = (
    "journal:",
    "publication:",
    "source:",
)
AUTHOR_PREFIXES = (
    "authors:",
    "author:",
    "by ",
)
DATE_PREFIXES = (
    "published:",
    "publication date:",
    "online publication date:",
    "date:",
)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
DATE_RE = re.compile(
    r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\s+\d{1,2},\s+\d{4}\b",
    re.IGNORECASE,
)


def _is_noise_link(text: str, href: str) -> bool:
    haystack = f"{text} {href}".lower()
    return any(pattern in haystack for pattern in NOISE_PATTERNS)


def _extract_prefixed_value(lines: list[str], prefixes: tuple[str, ...]) -> str:
    for line in lines:
        lower = line.lower()
        for prefix in prefixes:
            if lower.startswith(prefix):
                if ":" in line:
                    return collapse_ws(line.split(":", 1)[1])
                return collapse_ws(line[len(prefix) :])
    return ""


def _guess_journal_name(subject: str, lines: list[str]) -> str:
    labeled = _extract_prefixed_value(lines, JOURNAL_PREFIXES)
    if labeled:
        return labeled
    subject_clean = collapse_ws(subject)
    for marker in ("latest issue alert", "table of contents", "new issue alert", "latest issue", "new issue", "issue alert"):
        idx = subject_clean.lower().find(marker)
        if idx != -1:
            tail = subject_clean[idx + len(marker) :].strip(" :-|")
            if tail:
                return tail
    return ""


def _extract_doi(text: str, url: str) -> str:
    match = DOI_RE.search(text or "")
    if match:
        return match.group(0)
    match = DOI_RE.search(url or "")
    if match:
        return match.group(0)
    return ""


def parse_journal_email_articles(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract article-like records from newsletter-style journal emails."""
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
        fallback_title = collapse_ws(subject) or "Untitled journal article"
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
        lines = candidate.get("lines", [])
        journal_name = _guess_journal_name(subject, lines)
        authors = _extract_prefixed_value(lines, AUTHOR_PREFIXES)
        published = _extract_prefixed_value(lines, DATE_PREFIXES)
        if not published:
            match = DATE_RE.search(candidate["text"])
            if match:
                published = collapse_ws(match.group(0))
        summary = candidate["text"]
        for token in (candidate["title"], journal_name, authors, published):
            if token:
                summary = summary.replace(token, " ")
        summary = collapse_ws(summary)[:1200]
        items.append(
            {
                "title": candidate["title"],
                "link": candidate["link"],
                "journal_name": journal_name,
                "authors": authors,
                "published_hint": published,
                "summary": summary or collapse_ws(text)[:1200],
                "doi": _extract_doi(candidate["text"], candidate["link"]),
                "parsing_confidence": 0.8 if candidate["link"] and candidate["title"] != collapse_ws(subject) else 0.4,
            }
        )
    return items
