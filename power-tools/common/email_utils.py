#!/usr/bin/env python3
"""Helpers for conservative IMAP email ingestion."""

from __future__ import annotations

import datetime
import email
import html
import imaplib
import os
import re
from email.header import decode_header
from email.message import Message
from email.utils import parsedate_to_datetime
from typing import Any


TAG_RE = re.compile(r"<[^>]+>")
STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
WS_RE = re.compile(r"\s+")
URL_RE = re.compile(r"https?://[^\s<>\"]+")


def collapse_ws(text: str) -> str:
    return WS_RE.sub(" ", text or "").strip()


def strip_html(text: str) -> str:
    cleaned = STYLE_RE.sub(" ", text or "")
    cleaned = TAG_RE.sub(" ", cleaned)
    return collapse_ws(html.unescape(cleaned))


def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    parts: list[str] = []
    for fragment, encoding in decode_header(value):
        if isinstance(fragment, bytes):
            try:
                parts.append(fragment.decode(encoding or "utf-8", errors="replace"))
            except LookupError:
                parts.append(fragment.decode("utf-8", errors="replace"))
        else:
            parts.append(fragment)
    return collapse_ws("".join(parts))


def parse_message_date(raw_date: str | None) -> str:
    if not raw_date:
        return "unknown"
    try:
        parsed = parsedate_to_datetime(raw_date)
    except Exception:
        return "unknown"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.astimezone(datetime.timezone.utc).isoformat()


def extract_links(*values: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for value in values:
        for match in URL_RE.findall(value or ""):
            link = match.rstrip(").,;")
            if link and link not in seen:
                seen.add(link)
                links.append(link)
    return links


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def extract_message_text(message: Message) -> dict[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    if message.is_multipart():
        for part in message.walk():
            content_disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in content_disposition:
                continue
            content_type = (part.get_content_type() or "").lower()
            if content_type == "text/plain":
                text_parts.append(_decode_payload(part))
            elif content_type == "text/html":
                html_parts.append(_decode_payload(part))
    else:
        content_type = (message.get_content_type() or "").lower()
        decoded = _decode_payload(message)
        if content_type == "text/html":
            html_parts.append(decoded)
        else:
            text_parts.append(decoded)

    html_text = "\n".join(html_parts)
    plain_text = "\n".join(text_parts)
    if not plain_text and html_text:
        plain_text = strip_html(html_text)

    return {
        "text": collapse_ws(plain_text),
        "html": html_text,
        "html_text": strip_html(html_text),
    }


def build_message_key(mailbox: str, message_id: str, uid: str) -> str:
    return f"{mailbox}::{message_id or uid}"


def normalize_message_record(message: Message, route: dict[str, Any], mailbox: str, uid: str) -> dict[str, Any]:
    body = extract_message_text(message)
    subject = decode_mime_header(message.get("Subject"))
    sender = decode_mime_header(message.get("From"))
    published = parse_message_date(message.get("Date"))
    links = extract_links(body.get("text", ""), body.get("html_text", ""))
    snippet_source = body.get("text") or body.get("html_text") or subject
    message_id = collapse_ws(message.get("Message-ID", ""))
    summary = snippet_source[:1200]

    return {
        "source_type": "email_imap",
        "route_name": route.get("name", mailbox),
        "target": route.get("target", ""),
        "mailbox": mailbox,
        "gmail_label": route.get("gmail_label", mailbox),
        "message_id": message_id,
        "message_key": build_message_key(mailbox, message_id, uid),
        "imap_uid": uid,
        "subject": subject,
        "from": sender,
        "published": published,
        "summary": summary,
        "body_text": body.get("text", ""),
        "body_html": body.get("html", ""),
        "body_html_text": body.get("html_text", ""),
        "links": links,
        "link": links[0] if links else "",
        "tags": list(route.get("tags", [])),
    }


def connect_imap(host: str, port: int, username: str, password: str) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(host, port)
    client.login(username, password)
    return client


def load_imap_credentials(config: dict[str, Any]) -> tuple[str, str]:
    username_env = config.get("username_env", "GMAIL_IMAP_USERNAME")
    password_env = config.get("password_env", "GMAIL_IMAP_PASSWORD")
    username = os.environ.get(username_env, "").strip()
    password = os.environ.get(password_env, "").strip()
    if not username or not password:
        raise RuntimeError(
            f"Missing IMAP credentials. Set environment variables {username_env} and {password_env}."
        )
    return username, password


def route_matches(message_record: dict[str, Any], route: dict[str, Any]) -> bool:
    subject = (message_record.get("subject") or "").lower()
    sender = (message_record.get("from") or "").lower()
    if route.get("subject_contains"):
        expected = [str(value).lower() for value in route.get("subject_contains", [])]
        if not any(token in subject for token in expected):
            return False
    if route.get("from_contains"):
        expected = [str(value).lower() for value in route.get("from_contains", [])]
        if not any(token in sender for token in expected):
            return False
    return True


def search_uids(client: imaplib.IMAP4_SSL, include_seen: bool, lookback_days: int) -> list[str]:
    criteria = ["ALL" if include_seen else "UNSEEN"]
    if lookback_days > 0:
        since_date = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=lookback_days)).strftime(
            "%d-%b-%Y"
        )
        criteria.extend(["SINCE", since_date])
    status, data = client.uid("search", None, *criteria)
    if status != "OK":
        raise RuntimeError(f"IMAP search failed with status {status!r}")
    if not data or not data[0]:
        return []
    return [value for value in data[0].decode("utf-8", errors="replace").split() if value]
