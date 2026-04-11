#!/usr/bin/env python3
"""Conservative web scrapers for job sources."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from common.http_utils import fetch_bytes
from common.job_email_parser import classify_job_category


GOODWORK_LINK_RE = re.compile(r"/jobs/[^\"'#?]*-\d{4,}")
UNIVERSITY_AFFAIRS_LINK_RE = re.compile(r"job_id=\d+")
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


def _truncate_summary(text: str, limit: int = 220) -> str:
    cleaned = _clean(text)
    if len(cleaned) <= limit:
        return cleaned
    truncated = cleaned[:limit].rsplit(" ", 1)[0].strip()
    return f"{truncated}..."


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
    summary = _truncate_summary(" ".join(value for value in paragraphs[:3] if value))
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


def _extract_university_affairs_result(anchor, base_url: str) -> dict:
    title = _clean(anchor.get_text(" ", strip=True))
    link = urljoin(base_url, anchor.get("href", ""))
    metadata: list[str] = []
    for element in anchor.next_elements:
        if element == anchor:
            continue
        if getattr(element, "name", None) == "a" and element is not anchor and UNIVERSITY_AFFAIRS_LINK_RE.search(element.get("href", "")):
            break
        if isinstance(element, str):
            value = _clean(element)
            if value and value != title:
                metadata.append(value)
    organization = ""
    location = ""
    posted_date = ""
    _next_is_location = False
    _next_is_date = False
    for value in metadata:
        lowered = value.lower()
        if _next_is_location:
            location = value
            _next_is_location = False
            continue
        if _next_is_date:
            posted_date = value
            _next_is_date = False
            continue
        if lowered.startswith("location "):
            location = value.split(" ", 1)[1].strip()
        elif lowered == "location":
            _next_is_location = True
        elif lowered.startswith("posting date "):
            posted_date = value.split(" ", 2)[2].strip()
        elif lowered in ("posting date", "date posted"):
            _next_is_date = True
        elif not organization and not lowered.startswith("sort by") and "results" not in lowered:
            organization = value
    summary = _truncate_summary(" ".join(part for part in [organization, location, posted_date] if part))
    return {
        "source_type": "job_web",
        "source": "University Affairs",
        "title": title,
        "organization": organization,
        "location": location,
        "pay": "",
        "posted_date": posted_date,
        "application_deadline": "",
        "published": posted_date or "unknown",
        "category": "academic",
        "link": link,
        "summary": summary,
        "status": "open",
    }


def scrape_university_affairs_jobs(
    listing_url: str,
    max_items: int = 100,
    keywords: list[str] | None = None,
    fetcher=fetch_bytes,
) -> list[dict]:
    html = fetcher(listing_url).decode("utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    keyword_list = [value.lower() for value in (keywords or []) if str(value).strip()]
    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "")
        if not UNIVERSITY_AFFAIRS_LINK_RE.search(href):
            continue
        item = _extract_university_affairs_result(anchor, listing_url)
        if not item["title"] or not item["organization"]:
            continue
        haystack = f"{item['title']} {item['organization']} {item['location']} {item['summary']}".lower()
        if keyword_list and not any(keyword in haystack for keyword in keyword_list):
            continue
        if item["link"] in seen:
            continue
        seen.add(item["link"])
        items.append(item)
        if len(items) >= max_items:
            break
    return items
