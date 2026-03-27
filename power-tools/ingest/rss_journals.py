#!/usr/bin/env python3
"""Ingest journal RSS feeds into a local JSON snapshot."""

from __future__ import annotations

import datetime
from email.utils import parsedate_to_datetime
import re
import sys
from pathlib import Path
from urllib.parse import urlencode

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.http_utils import fetch_bytes, fetch_json
from common.email_source_registry import route_matches_target
from common.io_utils import CONFIGS_DIR, INGEST_DIR, STATE_DIR, dump_json, ensure_data_dirs, load_json, load_yaml
from common.journal_email_parser import parse_journal_email_articles
from common.record_utils import canonicalize_url, fingerprint_record, merge_records, normalize_date, normalize_title
from common.runtime import is_test_mode


HTML_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
DOI_RE = re.compile(r"(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", re.IGNORECASE)
OPENALEX_API_BASE = "https://api.openalex.org/works"
DEFAULT_OPENALEX_LOOKBACK_DAYS = 3
DEFAULT_OPENALEX_PER_PAGE = 50


def strip_html(text: str) -> str:
    return WS_RE.sub(" ", HTML_RE.sub(" ", text or "")).strip()


def parse_date(entry) -> str:
    for attr in ("published_parsed", "updated_parsed", "created_parsed"):
        value = getattr(entry, attr, None)
        if value:
            dt = datetime.datetime(*value[:6], tzinfo=datetime.timezone.utc)
            return dt.isoformat()
    for attr in ("published", "updated", "created"):
        raw = entry.get(attr)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(datetime.timezone.utc).isoformat()
            except Exception:
                pass
    return "unknown"


def article_key(link: str, title: str, published: str) -> str:
    return "||".join((link.strip(), title.strip().lower(), published.strip()))


def normalize_doi(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"^https?://(dx\.)?doi\.org/", "", raw, flags=re.IGNORECASE)
    match = DOI_RE.search(raw)
    if not match:
        return ""
    return match.group(1).rstrip(").,;]").lower()


def extract_doi(item: dict) -> str:
    for candidate in (item.get("doi", ""), item.get("canonical_link", ""), item.get("link", ""), item.get("summary", "")):
        doi = normalize_doi(str(candidate or ""))
        if doi:
            return doi
    return ""


def article_identity(item: dict) -> str:
    doi = extract_doi(item)
    if doi:
        return f"doi::{doi}"
    title = normalize_title(item.get("title", ""))
    published = normalize_date(item.get("published", ""))
    if title and published:
        return f"title-date::{title}||{published}"
    link = canonicalize_url(item.get("link", ""))
    if title or link or published:
        return f"fingerprint::{fingerprint_record(item.get('title', ''), item.get('link', ''), item.get('published', ''))}"
    return ""


def reconstruct_openalex_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    if not isinstance(inverted_index, dict):
        return ""
    positions: dict[int, str] = {}
    for word, offsets in inverted_index.items():
        if not isinstance(offsets, list):
            continue
        for offset in offsets:
            if isinstance(offset, int):
                positions[offset] = str(word)
    if not positions:
        return ""
    return " ".join(positions[index] for index in sorted(positions))


def openalex_to_article(work: dict, keyword: str) -> dict:
    authors = [authorship.get("author", {}).get("display_name", "").strip() for authorship in work.get("authorships", [])[:5]]
    authors = [author for author in authors if author]
    doi = normalize_doi(work.get("doi", ""))
    landing_page = (work.get("primary_location") or {}).get("landing_page_url", "")
    link = canonicalize_url(landing_page) or (f"https://doi.org/{doi}" if doi else canonicalize_url(work.get("id", "")))
    source = (work.get("primary_location") or {}).get("source", {}) or {}
    summary = reconstruct_openalex_abstract(work.get("abstract_inverted_index"))[:1200]
    publication_date = (work.get("publication_date") or "").strip()
    published = f"{publication_date}T00:00:00+00:00" if publication_date else "unknown"
    openalex_id = (work.get("id", "") or "").strip()
    article_key_link = link or canonicalize_url(openalex_id)
    return {
        "source_type": "journal_openalex",
        "feed": source.get("display_name", "") or "OpenAlex",
        "title": (work.get("display_name") or "").strip(),
        "link": link,
        "summary": summary,
        "published": published,
        "tags": ["openalex", keyword],
        "article_key": article_key(article_key_link, work.get("display_name", ""), published),
        "canonical_link": link,
        "authors": ", ".join(authors),
        "doi": doi,
        "openalex_id": openalex_id,
        "open_access": bool((work.get("open_access") or {}).get("is_oa", False)),
        "matched_keyword": keyword,
    }


def load_openalex_article_items(config: dict, seen_article_keys: set[str], seen_article_identities: set[str]) -> list[dict]:
    openalex_cfg = dict(config.get("openalex", {}) or {})
    if openalex_cfg.get("enabled", True) is False:
        return []

    lookback_days = int(openalex_cfg.get("lookback_days", DEFAULT_OPENALEX_LOOKBACK_DAYS))
    per_page = int(openalex_cfg.get("per_page", DEFAULT_OPENALEX_PER_PAGE))
    from_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=lookback_days)).date().isoformat()

    lab_profile = load_yaml(CONFIGS_DIR / "lab_profile.yaml")
    configured_keywords = [str(keyword).strip() for keyword in openalex_cfg.get("keywords", []) if str(keyword).strip()]
    keywords = configured_keywords or [str(keyword).strip() for keyword in lab_profile.get("research_interests", []) if str(keyword).strip()]
    mailto = str(openalex_cfg.get("mailto", "")).strip()

    items: list[dict] = []
    local_identities: set[str] = set()
    local_article_keys: set[str] = set()
    for keyword in keywords:
        params = {
            "search": f"\"{keyword}\"",
            "filter": f"from_publication_date:{from_date},is_retracted:false,type:article",
            "sort": "publication_date:desc",
            "per-page": per_page,
        }
        if mailto:
            params["mailto"] = mailto
        payload = fetch_json(f"{OPENALEX_API_BASE}?{urlencode(params)}")
        for work in payload.get("results", []):
            if not isinstance(work, dict):
                continue
            item = openalex_to_article(work, keyword)
            if not item.get("title"):
                continue
            identity = article_identity(item)
            if item["article_key"] in seen_article_keys:
                continue
            if identity and (identity in seen_article_identities or identity in local_identities):
                continue
            if item["article_key"] in local_article_keys:
                continue
            if identity:
                local_identities.add(identity)
            local_article_keys.add(item["article_key"])
            items.append(item)
    return items


def merge_journal_items(items: list[dict]) -> list[dict]:
    merged_by_identity: dict[str, dict] = {}
    order: list[str] = []
    for item in items:
        canonical_link = canonicalize_url(item.get("link", ""))
        if canonical_link:
            item["link"] = canonical_link
            item["canonical_link"] = canonical_link
        doi = extract_doi(item)
        if doi:
            item["doi"] = doi
        item.setdefault("provenance", [])
        item.setdefault("sources", [])
        if item.get("source_type") == "journal_rss":
            item["provenance"] = ["rss"]
            item["sources"] = [item.get("feed", "")]
        elif item.get("source_type") == "journal_email":
            item["provenance"] = ["email"]
            item["sources"] = [item.get("gmail_label", "") or item.get("feed", "")]
        elif item.get("source_type") == "journal_openalex":
            item["provenance"] = ["openalex"]
            item["sources"] = [item.get("feed", "") or "OpenAlex"]

        identity = article_identity(item)
        if not identity:
            identity = item.get("article_key", "")
        if identity not in merged_by_identity:
            merged_by_identity[identity] = item
            order.append(identity)
            continue
        merged_by_identity[identity] = merge_records(
            merged_by_identity[identity],
            item,
            preserve_keys=("article_key",),
        )
    return [merged_by_identity[key] for key in order]


def load_email_article_items(
    seen_article_keys: set[str],
    seen_article_identities: set[str] | None = None,
    seen_links: set[str] | None = None,
) -> list[dict]:
    payload = load_json(INGEST_DIR / "email_messages.json", default={"items": []}) or {}
    items: list[dict] = []
    email_seen_fingerprints: set[str] = set()
    identities = seen_article_identities or set()
    for message in payload.get("items", []):
        if not isinstance(message, dict):
            continue
        if not route_matches_target(message, "journal_articles"):
            continue
        parsed_items = parse_journal_email_articles(message)
        for parsed in parsed_items:
            title = (parsed.get("title") or "").strip()
            if not title:
                continue
            published = message.get("published", "unknown")
            link = (parsed.get("link") or "").strip()
            key = article_key(link or f"message:{message.get('message_id', '')}", title, published)
            if key in seen_article_keys:
                continue
            candidate_item = {"title": title, "link": link, "published": published, "doi": parsed.get("doi", "")}
            identity = article_identity(candidate_item)
            fingerprint = fingerprint_record(title, link, published)
            if fingerprint in email_seen_fingerprints:
                continue
            if identity and identity in identities:
                continue
            email_seen_fingerprints.add(fingerprint)
            if identity:
                identities.add(identity)
            items.append(
                {
                    "source_type": "journal_email",
                    "feed": parsed.get("journal_name") or message.get("route_name", message.get("mailbox", "Email")),
                    "title": title,
                    "link": link,
                    "summary": (parsed.get("summary") or message.get("summary") or message.get("body_text") or "")[:1200],
                    "published": published,
                    "tags": list(message.get("tags", [])),
                    "article_key": key,
                    "gmail_label": message.get("gmail_label", ""),
                    "message_id": message.get("message_id", ""),
                    "email_from": message.get("from", ""),
                    "authors": parsed.get("authors", ""),
                    "doi": parsed.get("doi", ""),
                    "published_hint": parsed.get("published_hint", ""),
                    "parsing_confidence": parsed.get("parsing_confidence", 0.0),
                    "canonical_link": canonicalize_url(link),
                }
            )
    return items


def sample_items() -> list[dict]:
    return [
        {
            "source_type": "journal_rss",
            "feed": "Methods in Ecology and Evolution",
            "title": "Sample: Benchmarking LLM-assisted habitat classification workflows",
            "link": "https://example.org/sample-llm-habitat-classification",
            "summary": "Sample article for test mode showing an AI-enabled ecology methods paper.",
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tags": ["ai", "methods", "test-mode"],
        },
        {
            "source_type": "journal_rss",
            "feed": "Restoration Ecology",
            "title": "Sample: Community-led restoration outcomes across urban wetlands",
            "link": "https://example.org/sample-urban-wetlands-restoration",
            "summary": "Sample article for test mode covering restoration practice and social dimensions.",
            "published": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "tags": ["restoration", "community", "test-mode"],
        },
    ]


def main() -> None:
    ensure_data_dirs()
    existing_state = load_json(STATE_DIR / "rss_seen_articles.json", default={}) or {}
    seen_article_keys = set(existing_state.get("seen_article_keys", []))
    seen_article_identities = set(existing_state.get("seen_article_identities", []))
    if is_test_mode():
        rss_items = sample_items()
        email_items = load_email_article_items(seen_article_keys, seen_article_identities)
        items = merge_journal_items(rss_items + email_items)
        dump_json(
            INGEST_DIR / "journal_articles.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": items,
                "test_mode": True,
            },
        )
        print(f"Wrote {len(items)} sample journal articles to {INGEST_DIR / 'journal_articles.json'}")
        print(f"[rss_journals] counts: rss={len(rss_items)}, email={len(email_items)}, openalex=0, merged={len(items)}")
        return

    config = load_yaml(CONFIGS_DIR / "journals.yaml")
    lookback_hours = int(config.get("lookback_hours", 48))
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=lookback_hours)
    items: list[dict] = []
    seen_links = set()

    failed_feeds: list[str] = []
    for feed in config.get("feeds", []):
        feed_name = feed.get("name", feed.get("url", "unknown"))
        try:
            payload = fetch_bytes(feed["url"])
        except Exception as exc:
            print(f"[rss_journals] WARNING: skipping {feed_name!r}: {exc}", file=sys.stderr)
            failed_feeds.append(feed_name)
            continue
        parsed = feedparser.parse(payload)
        for entry in parsed.entries[: int(feed.get("max_items", 50))]:
            published = parse_date(entry)
            if published != "unknown":
                dt = datetime.datetime.fromisoformat(published)
                if dt < cutoff:
                    continue

            link = entry.get("link", "").strip()
            title = entry.get("title", "").strip()
            if not link or link in seen_links:
                continue
            if not title:
                continue
            seen_links.add(link)
            key = article_key(link, title, published)
            if key in seen_article_keys:
                continue

            item = {
                "source_type": "journal_rss",
                "feed": feed_name,
                "title": title,
                "link": link,
                "summary": strip_html(entry.get("summary", entry.get("description", ""))),
                "published": published,
                "tags": feed.get("tags", []),
                "article_key": key,
                "canonical_link": canonicalize_url(link),
            }
            identity = article_identity(item)
            if identity and identity in seen_article_identities:
                continue
            items.append(item)

    rss_count = len(items)
    email_items = load_email_article_items(seen_article_keys, seen_article_identities, seen_links)
    openalex_items = load_openalex_article_items(config, seen_article_keys, seen_article_identities)
    items.extend(email_items)
    items.extend(openalex_items)
    items = merge_journal_items(items)
    dump_json(
        INGEST_DIR / "journal_articles.json",
        {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": items},
    )
    dump_json(
        STATE_DIR / "rss_seen_articles.json",
        {
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "seen_article_keys": sorted(seen_article_keys),
            "seen_article_identities": sorted(seen_article_identities),
            "pending_article_keys": sorted(item.get("article_key", "") for item in items if item.get("article_key")),
            "pending_article_identities": sorted(identity for identity in (article_identity(item) for item in items) if identity),
        },
    )
    print(f"Wrote {len(items)} journal articles to {INGEST_DIR / 'journal_articles.json'}")
    print(f"[rss_journals] counts: rss={rss_count}, email={len(email_items)}, openalex={len(openalex_items)}, merged={len(items)}")
    if failed_feeds:
        print(f"[rss_journals] {len(failed_feeds)} feed(s) unavailable: {', '.join(failed_feeds)}", file=sys.stderr)


if __name__ == "__main__":
    main()
