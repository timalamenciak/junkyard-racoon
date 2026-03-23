#!/usr/bin/env python3
"""Configurable LLM client for Groq, local, or other OpenAI-compatible endpoints."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common.http_utils import post_json
from common.io_utils import CONFIGS_DIR, load_yaml

def load_llm_config() -> dict[str, Any]:
    return load_yaml(CONFIGS_DIR / "llm.yaml")


def normalize_chat_endpoint(endpoint: str) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if not endpoint:
        return endpoint

    parsed = urlparse(endpoint)
    path = parsed.path.rstrip("/")
    if path.endswith("/chat/completions") or path.endswith("/messages") or path.endswith("/completions"):
        return endpoint
    if path.endswith("/v1"):
        return f"{endpoint}/chat/completions"
    if path:
        return endpoint
    return f"{endpoint}/v1/chat/completions"


def chat_completion(messages: list[dict[str, str]], max_tokens: int = 1200, temperature: float = 0.0) -> str:
    config = load_llm_config()
    provider = config.get("provider", "openai_compatible")
    endpoint = normalize_chat_endpoint(config.get("endpoint", ""))
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


def _extract_balanced_json_block(text: str, opener: str, closer: str) -> str | None:
    start = text.find(opener)
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(text)):
            char = text[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    candidate = text[start : idx + 1]
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break
        start = text.find(opener, start + 1)
    return None


def extract_json_payload(raw: str) -> Any:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    array_block = _extract_balanced_json_block(text, "[", "]")
    if array_block is not None:
        return json.loads(array_block)
    object_block = _extract_balanced_json_block(text, "{", "}")
    if object_block is not None:
        return json.loads(object_block)
    return json.loads(text)
