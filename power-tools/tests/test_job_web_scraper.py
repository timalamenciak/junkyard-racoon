#!/usr/bin/env python3

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from common.job_web_scraper import (
    parse_goodwork_detail,
    parse_goodwork_listing_urls,
    scrape_goodwork_jobs,
    scrape_university_affairs_jobs,
)


FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_parse_goodwork_listing_urls_collects_job_links() -> None:
    urls = parse_goodwork_listing_urls(load_fixture("goodwork_jobs_listing.html"), "https://www.goodwork.ca/jobs")
    assert urls[0] == "https://www.goodwork.ca/jobs/environmental-science-research-development-grant-writing-and-partnership-development-jobs-75619"
    assert len(urls) == 2


def test_parse_goodwork_detail_extracts_job_fields() -> None:
    item = parse_goodwork_detail(
        load_fixture("goodwork_job_detail.html"),
        "https://www.goodwork.ca/jobs/environmental-science-research-development-grant-writing-and-partnership-development-jobs-75619",
    )
    assert item["title"] == "Manager, Research Development and Partnerships"
    assert item["organization"] == "Bulkley Valley Research Centre"
    assert item["location"] == "Preferably Smithers, or northwest BC based"
    assert item["pay"] == "$40.87-$52.88/hr"
    assert item["application_deadline"].startswith("April 7, 2026")
    assert item["status"] == "Open/apply now"
    assert len(item["summary"]) < 240


def test_scrape_goodwork_jobs_uses_listing_and_detail_fetches() -> None:
    listing_url = "https://www.goodwork.ca/jobs"

    def fake_fetch(url: str) -> bytes:
        if url == listing_url:
            return load_fixture("goodwork_jobs_listing.html").encode("utf-8")
        return load_fixture("goodwork_job_detail.html").encode("utf-8")

    items = scrape_goodwork_jobs(listing_url, max_items=1, fetcher=fake_fetch)

    assert len(items) == 1
    assert items[0]["source"] == "GoodWork.ca"
    assert items[0]["category"] == "conservation"


def test_scrape_university_affairs_jobs_filters_to_relevant_keywords() -> None:
    def fake_fetch(_url: str) -> bytes:
        return load_fixture("university_affairs_jobs_listing.html").encode("utf-8")

    items = scrape_university_affairs_jobs(
        "https://universityaffairs.ca/search-jobs/",
        max_items=10,
        keywords=["environmental", "forestry", "conservation"],
        fetcher=fake_fetch,
    )

    assert len(items) == 2
    assert items[0]["source"] == "University Affairs"
    assert items[0]["category"] == "academic"
    assert items[0]["title"] == "Environmental Studies/Sciences - Assistant Professor"
    assert items[0]["organization"] == "University of Prince Edward Island"
