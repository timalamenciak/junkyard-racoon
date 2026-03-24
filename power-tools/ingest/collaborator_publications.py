#!/usr/bin/env python3
"""Poll ORCID public API for collaborator publications."""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.http_utils import fetch_json
from common.io_utils import CONFIGS_DIR, INGEST_DIR, dump_json, ensure_data_dirs, load_yaml
from common.runtime import is_test_mode


ORCID_API_BASE = "https://pub.orcid.org/v3.0"
# ORCID public API requires this header to return JSON instead of XML
ORCID_HEADERS = {"Accept": "application/json"}


def clean_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def fetch_orcid_works(orcid_id: str, days_back: int) -> list[dict]:
    """Return works published within the last *days_back* days for *orcid_id*."""
    cutoff = datetime.date.today() - datetime.timedelta(days=days_back)
    data = fetch_json(f"{ORCID_API_BASE}/{orcid_id}/works", extra_headers=ORCID_HEADERS)

    items = []
    for group in data.get("group", []):
        summaries = group.get("work-summary", [])
        if not summaries:
            continue
        # ORCID groups the same work from multiple sources; first entry is preferred
        w = summaries[0]

        # Parse publication date — only year is guaranteed
        pub_date = w.get("publication-date") or {}
        year_val  = (pub_date.get("year")  or {}).get("value")
        month_val = (pub_date.get("month") or {}).get("value") or "01"
        day_val   = (pub_date.get("day")   or {}).get("value") or "01"

        if not year_val:
            continue
        try:
            pub = datetime.date(int(year_val), int(month_val), int(day_val))
        except (ValueError, TypeError):
            continue
        if pub < cutoff:
            continue

        title = ((w.get("title") or {}).get("title") or {}).get("value", "")

        # Prefer DOI, fall back to URL recorded in ORCID record
        ext_ids = (w.get("external-ids") or {}).get("external-id", [])
        doi = next(
            (e["external-id-value"] for e in ext_ids if e.get("external-id-type") == "doi"),
            None,
        )
        link = (w.get("url") or {}).get("value") or (f"https://doi.org/{doi}" if doi else "")

        items.append(
            {
                "title": title,
                "link": link,
                "published": pub.isoformat(),
                "source": "orcid",
                "orcid_put_code": w.get("put-code"),
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
            "source": "orcid",
            "orcid_put_code": None,
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
        if not isinstance(collaborator, dict):
            print("[collaborator_publications] WARNING: collaborator entry is not a mapping, skipping", file=sys.stderr)
            continue
        name = clean_str(collaborator.get("name"))
        if not name:
            print("[collaborator_publications] WARNING: collaborator entry missing name, skipping", file=sys.stderr)
            continue
        orcid_id = clean_str(collaborator.get("orcid"))
        if not orcid_id:
            print(f"[collaborator_publications] WARNING: no orcid set for {name!r}, skipping", file=sys.stderr)
            continue
        try:
            works = fetch_orcid_works(orcid_id, days_back)
        except Exception as exc:
            print(f"[collaborator_publications] WARNING: ORCID fetch failed for {name!r} ({orcid_id}): {exc}", file=sys.stderr)
            items.append({"collaborator": name, "orcid": orcid_id, "error": str(exc)})
            continue
        for work in works:
            work["collaborator"] = name
            work["orcid"] = orcid_id
            items.append(work)

    dump_json(
        INGEST_DIR / "collaborator_publications.json",
        {"generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "items": items},
    )
    print(f"Wrote {len(items)} collaborator publication records to {INGEST_DIR / 'collaborator_publications.json'}")


if __name__ == "__main__":
    main()
