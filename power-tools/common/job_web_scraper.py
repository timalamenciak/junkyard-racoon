#!/usr/bin/env python3
"""Conservative web scrapers for job sources."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from common.http_utils import fetch_bytes
from common.job_email_parser import classify_job_category


GOODWORK_LINK_RE = re.compile(r"/jobs/[^\"'#?]*-\d{4,}")
STATUS_RE = re.compile(r"Current status:\s*([^\.]+)", re.IGNORECASE)
POSTED_RE = re.compile(r"Date posted:\s*([A-Za-z]{3,9}\s+\d{1,2}\s+\d{4})", re.IGNORECASE)
DETAIL_FIELD_PATTERNS = {
    "organization": re.compile(r"Organization:\s*(.+?)(?:Location:|Starting wage:|Salary:|Closing Date:|$)", re.IGNORECASE),
    "location": re.compile(r"Location:\s*(.+?)(?:Starting wage:|Salary:|Closing Date:|$)", re.IGNORECASE),
    "pay": re.compile(r"(?:Starting wage|Salary|Rate of pay):\s*(.+?)(?:Closing Date:|Current status:|$)", re.IGNORECASE),
    "application_deadline": re.compile(r"(?:Closing Date|Application Deadline):\s*(.+?)(?:Current status:|$)", re.IGNORECASE),
}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_detail_field(text: str, field_name: str) -> str:
    pattern = DETAIL_FIELD_PATTERNS[field_name]
    match = pattern.search(text)
    if not match:
        return ""
    value = _clean(match.group(1))
    return value.split(" How to Apply", 1)[0].split(" The Opportunity", 1)[0].strip()


def parse_goodwork_listing_urls(html: str, base_url: str) -> list[str]:
    matches = GOODWORK_LINK_RE.findall(html or "")
    urls: list[str] = []
    seen: set[str] = set()
    for match in matches:
        url = urljoin(base_url, match)
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def parse_goodwork_detail(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    text = _clean(soup.get_text("\n", strip=True))
    title = ""
    position_match = re.search(r"Position Title:\s*(.+?)(?:Organization:|Location:|Starting wage:|Closing Date:|$)", text, re.IGNORECASE)
    if position_match:
        title = _clean(position_match.group(1))
    elif soup.find(["h1", "h2"]):
        title = _clean(soup.find(["h1", "h2"]).get_text(" ", strip=True))
    organization = _extract_detail_field(text, "organization")
    location = _extract_detail_field(text, "location")
    pay = _extract_detail_field(text, "pay")
    application_deadline = _extract_detail_field(text, "application_deadline")
    posted_date = ""
    posted_match = POSTED_RE.search(text)
    if posted_match:
        posted_date = _clean(posted_match.group(1))
    status = ""
    status_match = STATUS_RE.search(text)
    if status_match:
        status = _clean(status_match.group(1))
    paragraphs = [_clean(node.get_text(" ", strip=True)) for node in soup.find_all("p")]
    summary = " ".join(value for value in paragraphs[:3] if value)[:1200]
    category = classify_job_category(title, summary + " " + text[:2000], organization) or "conservation"
    return {
        "source_type": "job_web",
        "source": "GoodWork.ca",
        "title": title,
        "organization": organization,
        "location": location,
        "pay": pay,
        "posted_date": posted_date,
        "application_deadline": application_deadline,
        "published": posted_date or "unknown",
        "category": category,
        "link": url,
        "summary": summary,
        "status": status,
    }


def scrape_goodwork_jobs(listing_url: str, max_items: int = 25, fetcher=fetch_bytes) -> list[dict]:
    listing_html = fetcher(listing_url).decode("utf-8", errors="replace")
    detail_urls = parse_goodwork_listing_urls(listing_html, listing_url)[:max_items]
    items: list[dict] = []
    for detail_url in detail_urls:
        detail_html = fetcher(detail_url).decode("utf-8", errors="replace")
        item = parse_goodwork_detail(detail_html, detail_url)
        if item.get("status") and "open" not in item["status"].lower():
            continue
        if not item.get("title") or not item.get("organization"):
            continue
        items.append(item)
    return items
