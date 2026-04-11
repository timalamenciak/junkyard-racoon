#!/usr/bin/env python3
"""Generic web scraper for grant and funding listing pages."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from common.http_utils import fetch_bytes


# Anchor text keywords that suggest a link is a grant/funding opportunity
GRANT_TEXT_KEYWORDS = (
    "grant",
    "fund",
    "award",
    "fellowship",
    "bursary",
    "scholarship",
    "call for proposal",
    "call for application",
    "open call",
    "rfp",
    "request for proposal",
    "opportunity",
    "apply now",
    "apply here",
    "competition",
)

# URL path fragment pattern suggesting a grant opportunity page
GRANT_PATH_RE = re.compile(
    r"/(?:grant|fund|award|fellowship|opportunit|call|apply|bursary|competition|rfp|programme|program)",
    re.IGNORECASE,
)

# Anchor text or hrefs to always skip
NOISE_KEYWORDS = (
    "login",
    "sign in",
    "sign up",
    "register",
    "privacy policy",
    "terms of use",
    "cookie",
    "contact us",
    "about us",
    "careers at",
    "unsubscribe",
    "newsletter",
    "subscribe",
    "twitter",
    "facebook",
    "linkedin",
    "instagram",
    "youtube",
    "share this",
    "print this",
    "skip to",
    "back to top",
)

# Matches "Deadline: January 15, 2026" or "Apply by: 2026-01-15" etc.
DEADLINE_RE = re.compile(
    r"(?:deadline|due date|closes?|closing date|apply by|applications?\s+due|submit by"
    r"|open until|review begins?|submissions?\s+due)"
    r"\s*[:\-]?\s*"
    r"((?:january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"\.?\s+\d{1,2},?\s*\d{4}"
    r"|\d{4}[-/]\d{2}[-/]\d{2}"
    r"|\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+\d{4})",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _truncate(text: str, limit: int = 300) -> str:
    cleaned = _clean(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rsplit(" ", 1)[0].strip() + "..."


def _is_noise(text: str, href: str) -> bool:
    haystack = f"{text} {href}".lower()
    return any(kw in haystack for kw in NOISE_KEYWORDS)


def _looks_like_grant(text: str, href: str) -> bool:
    if any(kw in text.lower() for kw in GRANT_TEXT_KEYWORDS):
        return True
    return bool(GRANT_PATH_RE.search(href))


def _extract_deadline(text: str) -> str:
    m = DEADLINE_RE.search(text)
    return _clean(m.group(1)) if m else ""


def _get_context(anchor) -> str:
    """Walk up the DOM to find a meaningful container and return its full text."""
    container = anchor
    anchor_text_len = len(_clean(anchor.get_text(" ", strip=True)))
    for parent in anchor.parents:
        tag = getattr(parent, "name", "")
        if tag in {"li", "tr", "article", "section", "div", "p", "td", "dd"}:
            parent_text = _clean(parent.get_text(" ", strip=True))
            if len(parent_text) >= anchor_text_len + 10:
                container = parent
                break
    return _clean(container.get_text(" ", strip=True))


def scrape_generic_grant_page(
    listing_url: str,
    source_name: str,
    keywords: list[str] | None = None,
    max_items: int = 20,
    fetcher=fetch_bytes,
) -> list[dict]:
    """Scrape a grant listing page for funding opportunities.

    Scans all anchor tags on the page for links that look like grant programs
    or calls. Extracts title, deadline, and context from surrounding HTML.
    Filters by an optional keyword list (OR-matched against title + context).
    Does not follow individual grant links — works entirely from the listing page.
    """
    html = fetcher(listing_url).decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    keyword_list = [kw.lower() for kw in (keywords or []) if kw.strip()]

    items: list[dict] = []
    seen_links: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = _clean(anchor.get("href", ""))
        if not href or href.startswith(("#", "javascript:", "mailto:")):
            continue
        full_url = urljoin(listing_url, href)
        if full_url in seen_links:
            continue

        title = _clean(anchor.get_text(" ", strip=True))
        if len(title) < 8 or len(title) > 250:
            continue
        if _is_noise(title, href):
            continue

        context_text = _get_context(anchor)

        # Must look like a grant link, or keyword must appear in context
        if not _looks_like_grant(title, href):
            if not keyword_list or not any(kw in context_text.lower() for kw in keyword_list):
                continue

        # If keywords provided, at least one must match title + context
        if keyword_list:
            haystack = f"{title} {context_text}".lower()
            if not any(kw in haystack for kw in keyword_list):
                continue

        deadline = _extract_deadline(context_text)
        summary = _truncate(context_text.replace(title, " "), 300)
        seen_links.add(full_url)
        items.append(
            {
                "source_type": "grant_web",
                "source": source_name,
                "title": title,
                "link": full_url,
                "summary": summary,
                "published": "unknown",
                "application_deadline": deadline,
                "deadline": deadline,
                "tags": ["web", "grants"],
                "sponsor": source_name,
                "parsing_confidence": 0.6,
            }
        )

        if len(items) >= max_items:
            break

    return items
