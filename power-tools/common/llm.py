#!/usr/bin/env python3
"""Configurable LLM client for Groq, local, or other OpenAI-compatible endpoints."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from common.http_utils import post_json
from common.io_utils import CONFIGS_DIR, load_yaml


JSON_ARRAY_RE = re.compile(r"\[[\s\S]*\]")
JSON_OBJECT_RE = re.compile(r"\{[\s\S]*\}")


def load_llm_config() -> dict[str, Any]:
    return load_yaml(CONFIGS_DIR / "llm.yaml")


def chat_completion(messages: list[dict[str, str]], max_tokens: int = 1200, temperature: float = 0.0) -> str:
    config = load_llm_config()
    provider = config.get("provider", "openai_compatible")
    endpoint = config.get("endpoint", "")
    api_key = config.get("api_key", "")
    model = config.get("model", "")

    if provider not in {"openai_compatible", "groq", "local"}:
        raise ValueError(f"Unsupported provider: {provider}")
    if not endpoint or not model:
        raise ValueError("llm.yaml must define 'endpoint' and 'model'")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    response = post_json(endpoint, payload, headers=headers, timeout=int(config.get("timeout", 60)))
    return response["choices"][0]["message"]["content"]


def extract_json_payload(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    array_match = JSON_ARRAY_RE.search(text)
    if array_match:
        return json.loads(array_match.group(0))
    object_match = JSON_OBJECT_RE.search(text)
    if object_match:
        return json.loads(object_match.group(0))
    return json.loads(text)
