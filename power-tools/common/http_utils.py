#!/usr/bin/env python3
"""Shared HTTP helpers with simple retry/backoff."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_TIMEOUT = 30
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 2


def fetch_bytes(url: str, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> bytes:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "power-tools/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(DEFAULT_BACKOFF * attempt)
    raise RuntimeError(f"request failed for {url}: {last_error}")


def fetch_json(url: str, timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> Any:
    return json.loads(fetch_bytes(url, timeout=timeout, retries=retries))


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str], timeout: int = DEFAULT_TIMEOUT, retries: int = DEFAULT_RETRIES) -> Any:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            detail = f"{exc.code} {exc.reason}"
            if body.strip():
                detail = f"{detail}: {body[:400]}"
            raise RuntimeError(f"POST {url} failed with HTTP {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(DEFAULT_BACKOFF * attempt)
    raise RuntimeError(f"POST {url} failed: {last_error}")
