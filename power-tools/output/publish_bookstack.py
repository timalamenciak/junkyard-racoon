#!/usr/bin/env python3
"""Publish the generated digest markdown to BookStack."""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, OUTPUT_DIR, load_json, load_yaml
from common.runtime import is_test_mode


def headers(config: dict) -> dict[str, str]:
    return {
        "Authorization": f"Token {config['token_id']}:{config['token_secret']}",
        "Content-Type": "application/json",
    }


def main() -> None:
    digest = load_json(OUTPUT_DIR / "daily_digest.json", default={})
    if not digest:
        raise SystemExit("No daily digest found. Run processing/daily_digest.py first.")

    output_cfg = load_yaml(CONFIGS_DIR / "output.yaml")
    bookstack = output_cfg.get("bookstack", {})
    page_name = f"Daily Lab Digest {digest.get('date', 'unknown')}"

    if is_test_mode():
        payload_out = {
            "page_url": f"{bookstack.get('url', 'https://example.org').rstrip('/')}/books/sample/pages/{page_name.lower().replace(' ', '-')}",
            "page_name": page_name,
            "test_mode": True,
        }
        (OUTPUT_DIR / "bookstack_publish.json").write_text(json.dumps(payload_out, indent=2), encoding="utf-8")
        print(payload_out["page_url"])
        return

    search_url = f"{bookstack['url']}/api/search?query={urllib.parse.quote(page_name)}&type=page"
    req = urllib.request.Request(search_url, headers=headers(bookstack))
    with urllib.request.urlopen(req, timeout=30) as resp:
        results = json.loads(resp.read())
    pages = results.get("data", [])
    existing = next((page for page in pages if page.get("name") == page_name), None)

    payload = {
        "name": page_name,
        "markdown": digest["markdown"],
        "book_id": bookstack["book_id"],
    }
    if bookstack.get("chapter_id"):
        payload["chapter_id"] = bookstack["chapter_id"]

    if existing:
        api_url = f"{bookstack['url']}/api/pages/{existing['id']}"
        method = "PUT"
    else:
        api_url = f"{bookstack['url']}/api/pages"
        method = "POST"

    req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers(bookstack), method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    page_url = f"{bookstack['url']}/books/{result.get('book_slug', '')}/pages/{result.get('slug', '')}"
    payload_out = {"page_url": page_url, "page_name": page_name, "test_mode": False}
    (OUTPUT_DIR / "bookstack_publish.json").write_text(json.dumps(payload_out, indent=2), encoding="utf-8")
    print(page_url)


if __name__ == "__main__":
    main()
