#!/usr/bin/env python3
"""Helpers for parsing job newsletter emails into structured openings."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from common.email_utils import collapse_ws, extract_links, strip_html


NOISE_PATTERNS = (
    "unsubscribe",
    "manage preferences",
    "view in browser",
    "sign in",
    "privacy policy",
    "terms of service",
)
DATE_PATTERN = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}"
POSTED_RE = re.compile(rf"\b(?:posted|posting date|posted date|date posted)\s*[:\-]?\s*({DATE_PATTERN}|\d{{4}}-\d{{2}}-\d{{2}})\b", re.IGNORECASE)
DEADLINE_RE = re.compile(
    rf"\b(?:deadline|apply by|application deadline|closing date|review begins|review of applications begins)\s*[:\-]?\s*({DATE_PATTERN}|\d{{4}}-\d{{2}}-\d{{2}})\b",
    re.IGNORECASE,
)
LOCATION_RE = re.compile(r"\b(?:location|based in|campus|city|remote|hybrid)\s*[:\-]?\s*([^|;]+)", re.IGNORECASE)
PAY_RE = re.compile(
    r"\b(?:salary|stipend|rate of pay|compensation|pay)\s*[:\-]?\s*((?:CAD|USD|EUR|GBP)?\s?\$?\d[\d,]*(?:\s*[-to]+\s*(?:CAD|USD|EUR|GBP)?\s?\$?\d[\d,]*)?(?:\s*(?:per year|/year|annually|per hour|/hour))?)",
    re.IGNORECASE,
)
ORG_LABEL_RE = re.compile(r"\b(?:organization|institution|department|employer)\s*[:\-]?\s*([^|;]+)", re.IGNORECASE)

ACADEMIC_ROLE_KEYWORDS = (
    "tenure-track",
    "tenure track",
    "assistant professor",
    "associate professor",
    "professor",
    "faculty",
    "postdoctoral",
    "postdoc",
    "phd",
    "doctoral",
    "masters",
    "msc",
    "graduate student",
    "research assistant",
    "research associate",
    "lecturer",
    "fellowship",
)
ACADEMIC_CONTEXT_KEYWORDS = (
    "biodiversity",
    "restoration",
    "conservation",
    "ecology",
    "wildlife",
    "environmental science",
    "natural resources",
)
CONSERVATION_KEYWORDS = (
    "conservation",
    "restoration",
    "biodiversity",
    "ecology",
    "wildlife",
    "habitat",
    "species at risk",
    "forestry",
    "wetland",
    "marine",
    "parks",
    "environmental",
)


def _is_noise_link(text: str, href: str) -> bool:
    haystack = f"{text} {href}".lower()
    return any(pattern in haystack for pattern in NOISE_PATTERNS)


def _extract_line_value(lines: list[str], prefixes: tuple[str, ...]) -> str:
    for line in lines:
        lowered = line.lower()
        for prefix in prefixes:
            if lowered.startswith(prefix):
                value = collapse_ws(line.split(":", 1)[-1] if ":" in line else line[len(prefix) :])
                if value:
                    return value
    return ""


def _first_match(pattern: re.Pattern[str], *values: str) -> str:
    for value in values:
        match = pattern.search(value or "")
        if match:
            return collapse_ws(match.group(1))
    return ""


def _guess_organization(title: str, lines: list[str], text: str) -> str:
    labeled = _first_match(ORG_LABEL_RE, text, "\n".join(lines))
    if labeled:
        return labeled
    filtered = [line for line in lines if collapse_ws(line) and collapse_ws(line) != title]
    for line in filtered:
        lowered = line.lower()
        if any(token in lowered for token in ("university", "college", "institute", "department", "society", "conservancy")):
            return collapse_ws(line)
    return filtered[0] if filtered else ""


def _normalize_date_hint(value: str) -> str:
    return collapse_ws(value.replace(" ,", ","))


def _guess_category(title: str, text: str, organization: str) -> str:
    haystack = f"{title} {text} {organization}".lower()
    has_academic_role = any(keyword in haystack for keyword in ACADEMIC_ROLE_KEYWORDS)
    has_academic_context = any(keyword in haystack for keyword in ACADEMIC_CONTEXT_KEYWORDS)
    has_conservation_context = any(keyword in haystack for keyword in CONSERVATION_KEYWORDS)
    if has_academic_role and has_academic_context:
        return "academic"
    if has_conservation_context:
        return "conservation"
    return ""


def parse_job_email_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    html = message.get("body_html", "") or ""
    text = message.get("body_text", "") or message.get("body_html_text", "") or ""
    subject = collapse_ws(message.get("subject", "") or "")
    soup = BeautifulSoup(html, "html.parser") if html else None

    candidates: list[dict[str, Any]] = []
    seen_links: set[str] = set()

    if soup is not None:
        for anchor in soup.find_all("a", href=True):
            href = collapse_ws(anchor.get("href", ""))
            title = collapse_ws(anchor.get_text(" ", strip=True))
            if len(title) < 8 or not href or href in seen_links or _is_noise_link(title, href):
                continue
            container = anchor
            for parent in anchor.parents:
                if getattr(parent, "name", "") in {"tr", "table", "div", "li", "td", "article", "section", "p"}:
                    parent_text = collapse_ws(parent.get_text(" ", strip=True))
                    if len(parent_text) >= len(title) + 15:
                        container = parent
                        break
            lines = [collapse_ws(value) for value in getattr(container, "stripped_strings", []) if collapse_ws(value)]
            candidates.append(
                {
                    "title": title,
                    "link": href,
                    "text": collapse_ws(container.get_text(" ", strip=True)),
                    "lines": lines,
                }
            )
            seen_links.add(href)

    if not candidates:
        fallback_links = extract_links(text, html)
        if fallback_links:
            fallback_text = text or strip_html(html)
            candidates.append(
                {
                    "title": subject or "Untitled job posting",
                    "link": fallback_links[0],
                    "text": fallback_text,
                    "lines": [collapse_ws(line) for line in fallback_text.splitlines() if collapse_ws(line)],
                }
            )

    items: list[dict[str, Any]] = []
    for candidate in candidates:
        title = collapse_ws(candidate.get("title", "")) or subject
        if not title:
            continue
        lines = list(candidate.get("lines", []))
        candidate_text = collapse_ws(candidate.get("text", "")) or text
        organization = _guess_organization(title, lines, candidate_text)
        location = _extract_line_value(lines, ("location", "based in", "campus")) or _first_match(LOCATION_RE, candidate_text)
        pay = _extract_line_value(lines, ("salary", "stipend", "rate of pay", "compensation", "pay")) or _first_match(PAY_RE, candidate_text)
        posted_date = _extract_line_value(lines, ("posted", "posting date", "posted date", "date posted")) or _first_match(POSTED_RE, candidate_text)
        deadline = _extract_line_value(lines, ("deadline", "apply by", "application deadline", "closing date", "review begins")) or _first_match(DEADLINE_RE, candidate_text)
        category = _guess_category(title, candidate_text, organization)
        if not category:
            continue
        summary = candidate_text.replace(title, " ")
        items.append(
            {
                "title": title,
                "organization": organization,
                "location": location,
                "pay": pay,
                "posted_date": _normalize_date_hint(posted_date),
                "application_deadline": _normalize_date_hint(deadline),
                "category": category,
                "link": collapse_ws(candidate.get("link", "")),
                "summary": collapse_ws(summary)[:1200] or collapse_ws(text)[:1200],
                "parsing_confidence": 0.8 if organization and candidate.get("link") else 0.5,
            }
        )
    return items
