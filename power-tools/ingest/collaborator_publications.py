#!/usr/bin/env python3
"""Poll OpenAlex for collaborator publications."""

from __future__ import annotations

import datetime
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.http_utils import fetch_json
from common.io_utils import CONFIGS_DIR, INGEST_DIR, dump_json, ensure_data_dirs, load_yaml
from common.runtime import is_test_mode


OPENALEX_AUTHOR_URL = "https://api.openalex.org/authors"
OPENALEX_WORKS_URL = "https://api.openalex.org/works"


def find_author_id(name: str) -> str | None:
    query = urllib.parse.quote(name)
    payload = fetch_json(f"{OPENALEX_AUTHOR_URL}?search={query}&per-page=5")
    for result in payload.get("results", []):
        display_name = (result.get("display_name") or "").lower()
        if name.lower() in display_name:
            return result.get("id")
    return None


def fetch_recent_works(author_id: str, days_back: int) -> list[dict]:
    cutoff = (datetime.date.today() - datetime.timedelta(days=days_back)).isoformat()
    query = urllib.parse.quote(f"authorships.author.id:{author_id},from_publication_date:{cutoff}")
    payload = fetch_json(f"{OPENALEX_WORKS_URL}?filter={query}&per-page=25")
    items = []
    for work in payload.get("results", []):
        items.append(
            {
                "title": work.get("display_name", ""),
                "link": work.get("primary_location", {}).get("landing_page_url") or work.get("doi", ""),
                "published": work.get("publication_date", "unknown"),
                "authors": [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])],
                "source": "openalex",
                "openalex_id": work.get("id", ""),
            }
        )
    return items


def sample_items() -> list[dict]:
    return [
        {
            "collaborator": "Sample Collaborator",
            "title": "Sample: Participatory restoration planning with decision support agents",
            "link": "https://example.org/sample-collaborator-paper",
            "published": datetime.date.today().isoformat(),
            "authors": ["Sample Collaborator", "A. Researcher"],
            "source": "openalex",
            "openalex_id": "https://openalex.org/Wsample123",
        }
    ]


def main() -> None:
    ensure_data_dirs()
    if is_test_mode():
        items = sample_items()
        dump_json(
            INGEST_DIR / "collaborator_publications.json",
            {
                "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "items": items,
                "test_mode": True,
            },
        )
        print(f"Wrote {len(items)} sample collaborator publication records to {INGEST_DIR / 'collaborator_publications.json'}")
        return

    config = load_yaml(CONFIGS_DIR / "collaborators.yaml")
    items: list[dict] = []
    days_back = int(config.get("days_back", 14))

    for collaborator in config.get("collaborators", []):
        name = collaborator["name"]
        author_id = collaborator.get("openalex_author_id") or find_author_id(name)
        if not author_id:
            items.append({"collaborator": name, "error": "openalex_author_not_found"})
            continue
        for work in fetch_recent_works(author_id, days_back):
            work["collaborator"] = name
            items.append(work)

    dump_json(
        INGEST_DIR / "collaborator_publications.json",
        {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": items},
    )
    print(f"Wrote {len(items)} collaborator publication records to {INGEST_DIR / 'collaborator_publications.json'}")


if __name__ == "__main__":
    main()
