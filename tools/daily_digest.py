#!/usr/bin/env python3
"""Read the latest rendered matrix digest from the power-tools output layer."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "power-tools" / "data" / "output"
SUMMARY_FILE = OUTPUT_DIR / "matrix_digest.txt"
FALLBACK_FILE = OUTPUT_DIR / "daily_digest.md"


def main() -> None:
    if SUMMARY_FILE.exists():
        print(SUMMARY_FILE.read_text(encoding="utf-8", errors="replace").strip())
        return
    if FALLBACK_FILE.exists():
        print(FALLBACK_FILE.read_text(encoding="utf-8", errors="replace").strip()[:4000])
        return
    print("No nightly digest artifact found yet. Run power-tools\\nightly_run.py first.")


if __name__ == "__main__":
    main()
