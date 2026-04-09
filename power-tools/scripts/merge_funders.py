#!/usr/bin/env python3
"""
Merge newfunders.yaml into grants.yaml and generate funding_sources.csv.

Usage:
    python power-tools/scripts/merge_funders.py
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import yaml

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
GRANTS_YAML = CONFIGS_DIR / "grants.yaml"
NEWFUNDERS_YAML = CONFIGS_DIR / "newfunders.yaml"
CSV_OUT = CONFIGS_DIR / "funding_sources.csv"


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main() -> None:
    grants = load_yaml(GRANTS_YAML)
    newfunders = load_yaml(NEWFUNDERS_YAML)

    new_sources: list[dict] = newfunders.get("sources", [])
    if not new_sources:
        print("No sources found in newfunders.yaml", file=sys.stderr)
        sys.exit(1)

    # Build a lookup of existing RSS source names (normalised) to detect overlaps
    existing_names = {
        s.get("name", "").strip().lower()
        for s in grants.get("sources", [])
    }

    # --- 1. Enrich existing RSS sources with priority metadata where names match ---
    funder_index: dict[str, dict] = {
        s.get("name", "").strip().lower(): s for s in new_sources
    }
    for rss_source in grants.get("sources", []):
        key = rss_source.get("name", "").strip().lower()
        if key in funder_index:
            nf = funder_index[key]
            rss_source.setdefault("priority", nf.get("priority", "standard"))
            rss_source.setdefault(
                "canada_eligible",
                nf.get("eligibility", {}).get("canadian_eligible", None),
            )
            rss_source.setdefault(
                "research_fit_score",
                nf.get("research_fit", {}).get("score", None),
            )

    # --- 2. Build the funders section (all entries from newfunders.yaml) ---
    funders_section: list[dict] = []
    for s in new_sources:
        entry: dict = {
            "name": s.get("name", ""),
            "grants_url": s.get("url", ""),      # listing page where calls are announced
            "homepage": s.get("homepage", ""),   # org root
            "priority": s.get("priority", "standard"),
            "canada_eligible": s.get("eligibility", {}).get("canadian_eligible", False),
            "canada_confidence": s.get("eligibility", {}).get("canadian_confidence", ""),
            "career_stage_eligible": s.get("eligibility", {}).get("career_stage_eligible", True),
            "research_fit_score": s.get("research_fit", {}).get("score", None),
            "tags": s.get("tags", []),
            "notes": s.get("notes", ""),
        }
        # Carry deadline hint if present
        if s.get("deadline_hint"):
            entry["deadline_hint"] = s["deadline_hint"]
        funders_section.append(entry)

    grants["funders"] = funders_section

    # --- 3. Write updated grants.yaml ---
    with open(GRANTS_YAML, "w", encoding="utf-8") as f:
        yaml.dump(grants, f, allow_unicode=True, sort_keys=False, default_flow_style=False)
    print(f"Updated {GRANTS_YAML} with {len(funders_section)} funders.")

    # --- 4. Generate funding_sources.csv ---
    csv_rows = []
    for s in new_sources:
        eligibility = s.get("eligibility", {})
        research_fit = s.get("research_fit", {})
        csv_rows.append({
            "name": s.get("name", ""),
            "grants_url": s.get("url", ""),
            "homepage": s.get("homepage", ""),
            "priority": s.get("priority", "standard"),
            "canada_eligible": eligibility.get("canadian_eligible", ""),
            "canada_confidence": eligibility.get("canadian_confidence", ""),
            "career_stage_eligible": eligibility.get("career_stage_eligible", ""),
            "research_fit_score": research_fit.get("score", ""),
            "research_fit_notes": research_fit.get("notes", "").replace("\n", " "),
            "tags": "; ".join(s.get("tags", [])),
            "notes": (s.get("notes") or "").replace("\n", " "),
            "deadline_hint": s.get("deadline_hint", ""),
            "paper_count": s.get("paper_count", ""),
        })

    fieldnames = [
        "name", "grants_url", "homepage", "priority", "canada_eligible", "canada_confidence",
        "career_stage_eligible", "research_fit_score", "research_fit_notes",
        "tags", "notes", "deadline_hint", "paper_count",
    ]

    with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(csv_rows)
    print(f"Wrote {CSV_OUT} with {len(csv_rows)} rows.")


if __name__ == "__main__":
    main()
