#!/usr/bin/env python3
"""Render a static HTML digest site with rolling daily digests and a live jobs table."""

from __future__ import annotations

import datetime
import email.utils
import html
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, OUTPUT_DIR, STATE_DIR, dump_json, ensure_data_dirs, load_json, load_yaml


STATE_PATH = STATE_DIR / "static_digest_site.json"
PODCAST_STATE_PATH = STATE_DIR / "podcast_state.json"
MASTODON_STATE_PATH = STATE_DIR / "mastodon_posted.json"
PODCAST_SRC_DIR = OUTPUT_DIR / "podcast"

MAX_HISTORY_DAYS = 60
JOB_RETENTION_DAYS = 120


def _clean(value: str) -> str:
    return html.escape(str(value or "").strip())


def _truncate_display(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return html.escape(text)
    shortened = text[:limit].rsplit(" ", 1)[0].strip()
    return html.escape(f"{shortened}...")


def _job_key(item: dict) -> str:
    return "||".join(
        [
            str(item.get("title", "")).strip().lower(),
            str(item.get("organization", "")).strip().lower(),
            str(item.get("location", "")).strip().lower(),
            str(item.get("link", "")).strip(),
        ]
    )


def _parse_date(value: str) -> datetime.date | None:
    raw = str(value or "").strip()
    if not raw or raw == "unknown":
        return None
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.datetime.strptime(raw, fmt).date()
        except Exception:
            continue
    try:
        return datetime.date.fromisoformat(raw[:10])
    except Exception:
        return None


def load_state() -> dict:
    state = load_json(STATE_PATH, default={}) or {}
    state.setdefault("digests", [])
    state.setdefault("jobs", [])
    return state


def save_state(state: dict) -> None:
    dump_json(STATE_PATH, state)


def merge_digest_history(existing: list[dict], incoming: dict, current_date: datetime.date) -> list[dict]:
    by_date = {str(item.get("date", "")): dict(item) for item in existing if isinstance(item, dict) and item.get("date")}
    by_date[incoming["date"]] = {
        "date": incoming["date"],
        "relevant_news": incoming.get("relevant_news", []),
        "relevant_articles": incoming.get("relevant_articles", []),
        "relevant_grants": incoming.get("relevant_grants", []),
        "prioritized_todos": incoming.get("prioritized_todos", []),
    }
    kept = [item for item in by_date.values() if _parse_date(item.get("date", ""))]
    kept = [item for item in kept if (current_date - _parse_date(item["date"])).days <= MAX_HISTORY_DAYS]
    kept.sort(key=lambda item: item["date"], reverse=True)
    return kept


def merge_jobs(existing: list[dict], incoming: list[dict], current_date: datetime.date) -> list[dict]:
    merged: dict[str, dict] = {}
    for item in existing:
        if isinstance(item, dict):
            merged[_job_key(item)] = dict(item)
    for item in incoming:
        if not isinstance(item, dict):
            continue
        key = _job_key(item)
        if not key.strip("|"):
            continue
        previous = merged.get(key, {})
        combined = dict(previous)
        combined.update(item)
        combined["first_seen_date"] = previous.get("first_seen_date", current_date.isoformat())
        combined["last_seen_date"] = current_date.isoformat()
        merged[key] = combined
    kept: list[dict] = []
    for item in merged.values():
        deadline = _parse_date(item.get("application_deadline", ""))
        first_seen = _parse_date(item.get("first_seen_date", ""))
        posted = _parse_date(item.get("posted_date", "")) or first_seen
        if deadline and deadline < current_date:
            continue
        if not deadline and posted and (current_date - posted).days > JOB_RETENTION_DAYS:
            continue
        kept.append(item)
    kept.sort(
        key=lambda item: (
            _parse_date(item.get("application_deadline", "")) or datetime.date.max,
            _parse_date(item.get("posted_date", "")) or datetime.date.max,
            str(item.get("title", "")).lower(),
        )
    )
    return kept


def collect_all_tags(jobs: list[dict]) -> list[str]:
    seen: set[str] = set()
    tags: list[str] = []
    for job in jobs:
        for tag in job.get("student_tags", []):
            t = str(tag).strip().lower()
            if t and t not in seen:
                seen.add(t)
                tags.append(t)
    return sorted(tags)


_GRANT_STATUS_CLASS = {
    "tracking": "status-tracking",
    "drafting": "status-drafting",
    "submitted": "status-submitted",
    "awarded": "status-awarded",
    "declined": "status-declined",
}


def render_item_list(items: list[dict], empty_text: str, score_key: str | None = None, action_key: str | None = None, item_type: str | None = None) -> str:
    if not items:
        return f"<p class='empty'>{_clean(empty_text)}</p>"
    parts: list[str] = ["<ul class='item-list'>"]
    for item in items:
        title = _clean(item.get("title", "Untitled"))
        link = str(item.get("link", "")).strip()
        is_manual = bool(item.get("always_surface"))
        if link:
            title_html = f"<a href='{html.escape(link, quote=True)}' target='_blank' rel='noreferrer'>{title}</a>"
        else:
            title_html = f"<span>{title}</span>"
        meta: list[str] = []
        if item_type == "articles":
            authors = _clean(item.get("authors", ""))
            journal = _clean(item.get("journal_name", "") or item.get("feed", ""))
            if authors:
                meta.append(authors)
            if journal:
                meta.append(f"<em>{journal}</em>")
        elif is_manual:
            status = str(item.get("status", "tracking")).lower()
            cls = _GRANT_STATUS_CLASS.get(status, "status-tracking")
            meta.append(f"<span class='grant-status {cls}'>{html.escape(status)}</span>")
            if item.get("deadline"):
                meta.append(f"<span class='grant-deadline'>due {_clean(item.get('deadline', ''))}</span>")
            if item.get("amount"):
                meta.append(f"<span class='grant-amount'>{_clean(item.get('amount', ''))}</span>")
        summary = str(item.get("llm_summary") or item.get("summary", "")).strip()
        if item_type == "jobs":
            summary = str(item.get("student_fit_reason") or item.get("llm_summary") or "").strip()
        summary_html = ""
        if summary:
            summary_html = f"<p class='item-summary'>{_truncate_display(summary, 280)}</p>"
        action_html = ""
        if action_key and item.get(action_key):
            label = "Next step" if action_key == "next_step" else "Recommended action"
            action_html = f"<p class='item-action'><strong>{html.escape(label)}:</strong> {_truncate_display(item.get(action_key, ''), 180)}</p>"
        parts.append("<li>")
        parts.append(title_html)
        if meta:
            parts.append(f"<div class='item-meta'>{' &middot; '.join(meta)}</div>")
        if summary_html:
            parts.append(summary_html)
        if action_html:
            parts.append(action_html)
        parts.append("</li>")
    parts.append("</ul>")
    return "".join(parts)


def render_todo_list(todos: list[dict]) -> str:
    if not todos:
        return (
            "<p class='empty todo-empty'>"
            "No tasks extracted. Check that <code>obsidian_vault_paths</code> in "
            "<code>lab_profile.yaml</code> points to a path accessible from this server, "
            "then re-run the pipeline."
            "</p>"
        )
    priority_class = {"high": "todo-high", "urgent": "todo-urgent", "medium": "todo-medium", "low": "todo-low"}
    parts = ["<ul class='todo-list'>"]
    for todo in todos[:15]:
        priority = str(todo.get("priority", "medium")).lower()
        cls = priority_class.get(priority, "todo-medium")
        task = _clean(todo.get("task", ""))
        project = _clean(todo.get("project", ""))
        rationale = _clean(todo.get("rationale", "") or todo.get("impact", ""))
        if not task:
            continue
        parts.append("<li class='todo-item'>")
        parts.append(f"<span class='todo-badge {cls}'>{html.escape(priority)}</span>")
        parts.append(f"<span class='todo-task'>{task}</span>")
        if project:
            parts.append(f"<span class='todo-project'>{project}</span>")
        if rationale:
            parts.append(f"<p class='todo-note'>{rationale}</p>")
        parts.append("</li>")
    parts.append("</ul>")
    return "".join(parts)


def render_jobs_controls(all_tags: list[str]) -> str:
    tag_chips = "".join(
        f"<button class='tag-chip' data-tag='{html.escape(tag, quote=True)}'>{html.escape(tag)}</button>"
        for tag in all_tags
    )
    return (
        "<div class='jobs-controls'>"
        "<div class='search-wrap'>"
        "<span class='search-icon'>&#x2315;</span>"
        "<input type='text' id='job-search' class='job-search' placeholder='Search jobs...' autocomplete='off'>"
        "<button class='clear-search' id='clear-search' aria-label='Clear search'>&#x2715;</button>"
        "</div>"
        f"<div class='tag-filter-row' id='tag-filter-row'>"
        "<button class='tag-chip active' data-tag='__all__'>All</button>"
        f"{tag_chips}"
        "</div>"
        "<div class='jobs-count'><span id='jobs-visible-count'></span></div>"
        "</div>"
    )


def render_jobs_table(items: list[dict]) -> str:
    rows = [
        "<table class='jobs-table' id='jobs-table'>"
        "<thead><tr>"
        "<th data-col='0'>Title <span class='sort-icon'></span></th>"
        "<th data-col='1'>Organization <span class='sort-icon'></span></th>"
        "<th data-col='2'>Location <span class='sort-icon'></span></th>"
        "<th data-col='3'>Rate of Pay <span class='sort-icon'></span></th>"
        "<th data-col='4'>Posted <span class='sort-icon'></span></th>"
        "<th data-col='5'>Deadline <span class='sort-icon'></span></th>"
        "<th data-col='6'>Student Fit <span class='sort-icon'></span></th>"
        "<th data-col='7'>Tags</th>"
        "</tr></thead><tbody>"
    ]
    if not items:
        rows.append("<tr><td colspan='8' class='empty-cell'>No open positions right now.</td></tr>")
    for item in items:
        raw_title = str(item.get("title", "Untitled role")).strip()
        title = _truncate_display(raw_title, 78)
        link = str(item.get("link", "")).strip()
        if link:
            title = (
                f"<a href='{html.escape(link, quote=True)}' target='_blank' rel='noreferrer' "
                f"title='{html.escape(raw_title, quote=True)}'>{title}</a>"
            )
        score = ""
        if item.get("student_relevance_score") is not None:
            try:
                score = f"{int(float(item.get('student_relevance_score', 0.0)) * 100)}%"
            except Exception:
                score = ""
        fit_reason = _truncate_display(item.get("student_fit_reason", ""), 90) if item.get("student_fit_reason") else ""
        fit_cell = ""
        if score or fit_reason:
            fit_parts = []
            if score:
                fit_parts.append(f"<code class='score-badge'>{html.escape(score)}</code>")
            if fit_reason:
                fit_parts.append(f"<span>{fit_reason}</span>")
            fit_cell = "".join(fit_parts)
        tags = [str(value).strip() for value in item.get("student_tags", []) if str(value).strip()]
        tag_html = " ".join(
            f"<span class='job-tag' data-tag='{html.escape(tag.lower(), quote=True)}'>{html.escape(tag)}</span>"
            for tag in tags[:6]
        )
        tags_search = " ".join(tags).lower()
        rows.append(
            f"<tr data-tags='{html.escape(tags_search, quote=True)}'>"
            f"<td data-label='Title'>{title}</td>"
            f"<td data-label='Organization' title='{html.escape(str(item.get('organization', '')), quote=True)}'>{_truncate_display(item.get('organization', ''), 42)}</td>"
            f"<td data-label='Location' title='{html.escape(str(item.get('location', '')), quote=True)}'>{_truncate_display(item.get('location', ''), 34)}</td>"
            f"<td data-label='Rate of Pay' title='{html.escape(str(item.get('pay', '')), quote=True)}'>{_truncate_display(item.get('pay', '') or item.get('pay_normalized', ''), 28)}</td>"
            f"<td data-label='Posted'>{_truncate_display(item.get('posted_date', ''), 18)}</td>"
            f"<td data-label='Deadline' title='{html.escape(str(item.get('application_deadline', '')), quote=True)}'>{_truncate_display(item.get('application_deadline', ''), 26)}</td>"
            f"<td data-label='Student Fit' class='fit-cell'>{fit_cell}</td>"
            f"<td data-label='Tags' class='tags-cell'>{tag_html}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "".join(rows)


def render_raccoon_badge() -> str:
    return """
<svg class='raccoon-badge' viewBox='0 0 220 180' role='img' aria-label='Raccoon illustration'>
  <defs>
    <linearGradient id='fur' x1='0%' y1='0%' x2='100%' y2='100%'>
      <stop offset='0%' stop-color='#7a7f85'/>
      <stop offset='100%' stop-color='#51565d'/>
    </linearGradient>
  </defs>
  <ellipse cx='58' cy='38' rx='16' ry='26' fill='#51565d' transform='rotate(-25 58 38)'/>
  <ellipse cx='162' cy='38' rx='16' ry='26' fill='#51565d' transform='rotate(25 162 38)'/>
  <ellipse cx='110' cy='86' rx='76' ry='62' fill='url(#fur)'/>
  <ellipse cx='110' cy='95' rx='56' ry='44' fill='#d9d2c4'/>
  <path d='M58 80c18-18 86-18 104 0-8 24-34 38-52 38S66 104 58 80Z' fill='#23262b'/>
  <ellipse cx='82' cy='82' rx='14' ry='18' fill='#0e1013'/>
  <ellipse cx='138' cy='82' rx='14' ry='18' fill='#0e1013'/>
  <circle cx='84' cy='82' r='4' fill='#fff7ea'/>
  <circle cx='136' cy='82' r='4' fill='#fff7ea'/>
  <ellipse cx='110' cy='108' rx='10' ry='8' fill='#2f3338'/>
  <path d='M100 120c8 8 12 8 20 0' stroke='#2f3338' stroke-width='4' fill='none' stroke-linecap='round'/>
  <path d='M32 132c30 12 126 12 156 0' stroke='#2a6048' stroke-width='10' fill='none' stroke-linecap='round'/>
</svg>
"""


def render_rss_feed(state: dict, public_url: str) -> str:
    """Render an RSS 2.0 feed with the 3 most recent grants, news, and articles."""
    digests = state.get("digests", [])
    news = flatten_items(digests, "relevant_news")[:3]
    articles = flatten_items(digests, "relevant_articles")[:3]
    grants = flatten_items(digests, "relevant_grants")[:3]

    def _rss_date(date_str: str) -> str:
        d = _parse_date(date_str)
        if d is None:
            return email.utils.formatdate(usegmt=True)
        dt = datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc)
        return email.utils.format_datetime(dt)

    def _item_xml(item: dict, category: str) -> str:
        title = str(item.get("title", "Untitled")).strip()
        link = str(item.get("link", "")).strip()
        date_str = str(item.get("_digest_date", "")).strip()
        summary = str(item.get("llm_summary") or item.get("summary", "")).strip()
        title_esc = html.escape(title)
        desc_esc = html.escape(summary) if summary else title_esc
        pub_date = _rss_date(date_str)
        guid_val = link if link else f"{public_url}#{category.lower()}-{title[:40]}"
        link_elem = f"<link>{html.escape(link, quote=True)}</link>" if link else ""
        return (
            "<item>"
            f"<title>{title_esc}</title>"
            f"{link_elem}"
            f"<description>{desc_esc}</description>"
            f"<category>{html.escape(category)}</category>"
            f"<pubDate>{pub_date}</pubDate>"
            f"<guid isPermaLink=\"{'true' if link else 'false'}\">{html.escape(guid_val, quote=True)}</guid>"
            "</item>"
        )

    items_xml = (
        [_item_xml(i, "News") for i in news]
        + [_item_xml(i, "Articles") for i in articles]
        + [_item_xml(i, "Grants") for i in grants]
    )
    feed_url = public_url.rstrip("/") + "/feed.xml"
    now = email.utils.formatdate(usegmt=True)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        "<channel>"
        "<title>Racoon Lab Research Digest</title>"
        f"<link>{html.escape(public_url)}</link>"
        "<description>Recent grants, news, and articles from the Racoon Lab daily digest.</description>"
        f"<lastBuildDate>{now}</lastBuildDate>"
        f'<atom:link href="{html.escape(feed_url, quote=True)}" rel="self" type="application/rss+xml"/>'
        + "".join(items_xml)
        + "</channel></rss>"
    )


def flatten_items(digests: list[dict], key: str) -> list[dict]:
    """Flatten items from all digests under `key`, annotated with digest date. Deduplicates by link/title."""
    result = []
    seen: set[str] = set()
    for digest in digests:
        date = digest.get("date", "")
        for item in digest.get(key, []):
            if not isinstance(item, dict):
                continue
            link = str(item.get("link", "")).strip()
            title = str(item.get("title", "")).strip().lower()
            dedup_key = link or title
            if dedup_key and dedup_key in seen:
                continue
            if dedup_key:
                seen.add(dedup_key)
            annotated = dict(item)
            annotated["_digest_date"] = date
            result.append(annotated)
    return result


def _tip(title_html: str, summary: str) -> str:
    """Wrap title_html in a tooltip span if summary is non-empty."""
    if not summary:
        return title_html
    tip_text = html.escape(summary[:300])
    return f"<span class='tip'>{title_html}<span class='tip-text'>{tip_text}</span></span>"


def render_news_table(items: list[dict]) -> str:
    if not items:
        return "<p class='empty'>No news items found.</p>"
    rows = [
        "<table class='digest-table'>"
        "<thead><tr><th>Date</th><th>Title</th></tr></thead><tbody>"
    ]
    for item in items:
        date = _clean(item.get("_digest_date", ""))
        title = _clean(item.get("title", "Untitled"))
        link = str(item.get("link", "")).strip()
        summary = str(item.get("llm_summary") or item.get("summary", "")).strip()
        if link:
            title_html = f"<a href='{html.escape(link, quote=True)}' target='_blank' rel='noreferrer'>{title}</a>"
        else:
            title_html = f"<span>{title}</span>"
        rows.append(f"<tr><td class='date-cell'>{date}</td><td>{_tip(title_html, summary)}</td></tr>")
    rows.append("</tbody></table>")
    return "".join(rows)


def render_articles_table(items: list[dict]) -> str:
    if not items:
        return "<p class='empty'>No articles found.</p>"
    rows = [
        "<table class='digest-table'>"
        "<thead><tr><th>Date</th><th>Title</th><th>Authors</th><th>Journal</th></tr></thead><tbody>"
    ]
    for item in items:
        date = _clean(item.get("_digest_date", ""))
        title = _clean(item.get("title", "Untitled"))
        link = str(item.get("link", "")).strip()
        authors = _clean(item.get("authors", ""))
        journal = _clean(item.get("journal_name", "") or item.get("feed", ""))
        summary = str(item.get("llm_summary") or item.get("summary", "")).strip()
        if link:
            title_html = f"<a href='{html.escape(link, quote=True)}' target='_blank' rel='noreferrer'>{title}</a>"
        else:
            title_html = f"<span>{title}</span>"
        rows.append(
            f"<tr><td class='date-cell'>{date}</td>"
            f"<td>{_tip(title_html, summary)}</td>"
            f"<td class='muted-cell'>{authors}</td>"
            f"<td class='muted-cell'><em>{journal}</em></td></tr>"
        )
    rows.append("</tbody></table>")
    return "".join(rows)


def render_grants_table(items: list[dict]) -> str:
    if not items:
        return "<p class='empty'>No grant opportunities found.</p>"
    rows = [
        "<table class='digest-table'>"
        "<thead><tr><th>Date</th><th>Title</th><th>Status</th><th>Deadline</th><th>Amount</th></tr></thead><tbody>"
    ]
    for item in items:
        date = _clean(item.get("_digest_date", ""))
        title = _clean(item.get("title", "Untitled"))
        link = str(item.get("link", "")).strip()
        is_manual = bool(item.get("always_surface"))
        summary = str(item.get("llm_summary") or item.get("summary", "")).strip()
        if link:
            title_html = f"<a href='{html.escape(link, quote=True)}' target='_blank' rel='noreferrer'>{title}</a>"
        else:
            title_html = f"<span>{title}</span>"
        if is_manual:
            status = str(item.get("status", "tracking")).lower()
            cls = _GRANT_STATUS_CLASS.get(status, "status-tracking")
            status_html = f"<span class='grant-status {cls}'>{html.escape(status)}</span>"
        else:
            status_html = ""
        deadline = _clean(item.get("deadline", "") or item.get("application_deadline", ""))
        amount = _clean(item.get("amount", ""))
        rows.append(
            f"<tr><td class='date-cell'>{date}</td>"
            f"<td>{_tip(title_html, summary)}</td>"
            f"<td>{status_html}</td>"
            f"<td class='muted-cell'>{deadline}</td>"
            f"<td class='muted-cell'>{amount}</td></tr>"
        )
    rows.append("</tbody></table>")
    return "".join(rows)


def render_toots_section(mastodon_state: dict, instance_url: str) -> str:
    toots = mastodon_state.get("toots", [])
    if not toots:
        return ""
    posted_date = _clean(mastodon_state.get("posted_date", ""))
    post_ids = mastodon_state.get("post_ids", [])
    parts = [
        "<section id='toots' class='content-card'>",
        "<div class='section-heading'><h2>Latest Toots</h2>",
        f"<span class='section-tag'>{posted_date}</span></div>",
        "<div class='toot-grid'>",
    ]
    for i, toot in enumerate(toots):
        post_id = post_ids[i] if i < len(post_ids) else ""
        toot_text = html.escape(str(toot)).replace("\n", "<br>")
        permalink = ""
        if instance_url and post_id:
            toot_url = f"{instance_url.rstrip('/')}/@junkyard_racoon/{post_id}"
            permalink = f"<a href='{html.escape(toot_url, quote=True)}' target='_blank' rel='noreferrer' class='toot-link'>view ↗</a>"
        parts.append(f"<div class='toot-card'><p>{toot_text}</p>{permalink}</div>")
    parts.append("</div></section>")
    return "".join(parts)


def render_metric_chips(digests: list[dict], jobs: list[dict]) -> str:
    recent = digests[:7]
    news_count = sum(len(item.get("relevant_news", [])) for item in recent)
    article_count = sum(len(item.get("relevant_articles", [])) for item in recent)
    grant_count = sum(len(item.get("relevant_grants", [])) for item in recent)
    return "".join(
        [
            f"<div class='metric-chip'><span>Open jobs</span><strong>{len(jobs)}</strong></div>",
            f"<div class='metric-chip'><span>News this week</span><strong>{news_count}</strong></div>",
            f"<div class='metric-chip'><span>Articles this week</span><strong>{article_count}</strong></div>",
            f"<div class='metric-chip'><span>Grants this week</span><strong>{grant_count}</strong></div>",
        ]
    )


def render_digest_section(digest: dict) -> str:
    digest_date = _clean(digest.get("date", "unknown"))
    todos = digest.get("prioritized_todos", [])
    todos_html = (
        "<div class='todos-section'>"
        "<h3>Priority Tasks</h3>"
        + render_todo_list(todos)
        + "</div>"
    )
    return (
        f"<section id='{digest_date}' class='digest-card'>"
        "<div class='section-heading'>"
        f"<h2>{digest_date}</h2>"
        "<span class='section-tag'>Daily salvage</span>"
        "</div>"
        "<div class='digest-grid'>"
        "<div class='column-card'><h3>News</h3>"
        + render_item_list(digest.get("relevant_news", []), "No relevant news items.", None, None, item_type="news")
        + "</div>"
        "<div class='column-card'><h3>Articles</h3>"
        + render_item_list(digest.get("relevant_articles", []), "No high-relevance papers.", "relevance_score", "recommended_action", item_type="articles")
        + "</div>"
        "<div class='column-card'><h3>Grants</h3>"
        + render_item_list(digest.get("relevant_grants", []), "No high-fit grant opportunities.", "relevance_score", "next_step", item_type="grants")
        + "</div>"
        "</div>"
        + todos_html
        + "</section>"
    )


_CSS = """
  :root {
    --bg: #ffffff;
    --ink: #111110;       /* 18:1 on white */
    --muted: #3d3a37;     /* 11:1 on white — body secondary text */
    --faint: #5e5b57;     /* 7:1 on white — dates, labels, metadata */
    --accent: #1b5436;    /* 8.5:1 on white — links and highlights */
    --line: #ccc8c2;      /* decorative borders, no contrast requirement */
    --panel: #f5f4f1;     /* hover backgrounds */
    --mono: 'JetBrains Mono','Fira Mono','Courier New',monospace;
  }
  * { box-sizing: border-box; }
  body { background: var(--bg); color: var(--ink); font-family: Georgia,'Times New Roman',serif; max-width: 860px; margin: 0 auto; padding: 2rem 1.5rem 4rem; font-size: 1rem; line-height: 1.65; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  code { font-family: var(--mono); font-size: 0.82em; color: var(--muted); }
  hr { border: none; border-top: 1px solid var(--line); margin: 2rem 0; }
  h1, h2, h3 { font-weight: normal; }
  .empty { color: var(--muted); font-size: 0.9rem; }

  /* ── Site header ── */
  .site-header { display: flex; justify-content: space-between; align-items: baseline; flex-wrap: wrap; gap: 0.4rem; }
  .site-title { font-family: var(--mono); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.2em; color: var(--muted); }
  .site-title a { color: var(--muted); }
  .site-title a:hover { color: var(--accent); text-decoration: none; }
  .site-date { font-family: var(--mono); font-size: 0.72rem; color: var(--faint); }
  .site-nav { display: flex; flex-wrap: wrap; gap: 0 1.25rem; margin: 0.6rem 0 0; }
  .site-nav a { font-family: var(--mono); font-size: 0.72rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.08em; }
  .site-nav a:hover { color: var(--accent); text-decoration: none; }

  /* ── Today's briefing ── */
  .briefing h2 { font-size: 1.05rem; margin: 0 0 1.5rem; border-bottom: 1px solid var(--ink); padding-bottom: 0.35rem; }
  .briefing-section { margin-bottom: 1.5rem; }
  .briefing-section h3 { font-family: var(--mono); font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.12em; color: var(--faint); margin: 0 0 0.6rem; }
  .briefing-list { list-style: none; padding: 0; margin: 0; }
  .briefing-list li { padding: 0.55rem 0; border-bottom: 1px dotted var(--line); }
  .briefing-list li:last-child { border-bottom: none; }
  .briefing-item-title { font-size: 0.95rem; line-height: 1.4; }
  .briefing-item-meta { font-family: var(--mono); font-size: 0.7rem; color: var(--faint); margin: 0.15rem 0 0.25rem; }
  .briefing-item-summary { font-size: 0.86rem; color: var(--muted); margin: 0; line-height: 1.5; }

  /* ── Archive sections ── */
  .archive-section { margin-top: 2rem; }
  .archive-section > h2 { font-family: var(--mono); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.12em; color: var(--muted); margin: 0 0 0.75rem; display: flex; align-items: center; gap: 0.75rem; }
  .archive-section > h2::after { content: ''; flex: 1; border-bottom: 1px solid var(--line); }
  .count-badge { font-size: 0.68rem; color: var(--faint); font-weight: normal; }

  /* ── Archive tables ── */
  .digest-table { width: 100%; border-collapse: collapse; font-size: 0.88rem; }
  .digest-table th { text-align: left; font-family: var(--mono); font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--faint); padding: 4px 8px 8px; border-bottom: 1px solid var(--line); font-weight: normal; }
  .digest-table td { padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
  .digest-table tbody tr:hover td { background: var(--panel); }
  .digest-table a { color: var(--ink); }
  .digest-table a:hover { color: var(--accent); text-decoration: underline; }
  .date-cell { white-space: nowrap; color: var(--faint); font-family: var(--mono); font-size: 0.72rem; width: 88px; }
  .muted-cell { color: var(--muted); font-size: 0.82rem; }

  /* ── Hover tooltip ── */
  .tip { position: relative; display: inline; }
  .tip .tip-text { display: none; position: absolute; left: 0; top: calc(100% + 4px); background: #23262b; color: #f4efe6; padding: 8px 12px; border-radius: 6px; font-size: 0.78rem; line-height: 1.5; width: 300px; max-width: 80vw; white-space: normal; z-index: 200; pointer-events: none; box-shadow: 0 4px 16px rgba(0,0,0,0.25); font-family: Georgia,serif; }
  .tip:hover .tip-text { display: block; }

  /* ── Grant status badges ── */
  .grant-status, .grant-deadline, .grant-amount { display: inline-block; font-size: 0.68rem; font-family: var(--mono); padding: 1px 5px; line-height: 1.5; }
  .grant-deadline { color: #6b3d00; } /* 8:1 on white */
  .grant-amount { color: var(--muted); }
  .status-tracking { background: #eef0fa; color: #1e2e7a; }   /* 9.5:1 */
  .status-drafting { background: #fdf3e0; color: #6b3d00; }   /* 8:1 */
  .status-submitted { background: #e6f2eb; color: #1b5436; }  /* 8.5:1 */
  .status-awarded { background: #e2f5e8; color: #14472a; }    /* 9.5:1 */
  .status-declined { background: #fae8e8; color: #6e1212; }   /* 9:1 */

  /* ── Jobs controls ── */
  .jobs-controls { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin: 0.75rem 0; }
  .search-wrap { position: relative; flex: 1; min-width: 180px; max-width: 320px; }
  .search-icon { position: absolute; left: 10px; top: 50%; transform: translateY(-50%); color: var(--faint); font-size: 0.9rem; pointer-events: none; }
  .job-search { width: 100%; padding: 6px 28px 6px 28px; border: 1px solid var(--line); border-radius: 3px; background: #fff; font-size: 0.84rem; font-family: var(--mono); color: var(--ink); outline: none; }
  .job-search:focus { border-color: var(--accent); }
  .clear-search { position: absolute; right: 8px; top: 50%; transform: translateY(-50%); background: none; border: none; color: var(--faint); cursor: pointer; font-size: 0.8rem; padding: 2px 4px; display: none; }
  .clear-search.visible { display: block; }
  .tag-filter-row { display: flex; flex-wrap: wrap; gap: 4px; }
  .tag-chip { display: inline-block; padding: 2px 8px; border: 1px solid var(--line); color: var(--muted); font-size: 0.68rem; font-family: var(--mono); cursor: pointer; background: transparent; transition: background 0.1s; }
  .tag-chip:hover { background: var(--panel); }
  .tag-chip.active { background: var(--accent); border-color: var(--accent); color: #fff; }
  .jobs-count { font-size: 0.7rem; font-family: var(--mono); color: var(--faint); }

  /* ── Jobs table ── */
  .jobs-table { width: 100%; border-collapse: collapse; font-size: 0.84rem; table-layout: fixed; }
  .jobs-table th, .jobs-table td { border-bottom: 1px solid var(--line); padding: 7px 8px; text-align: left; vertical-align: top; }
  .jobs-table thead th { font-family: var(--mono); font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--faint); font-weight: normal; cursor: pointer; user-select: none; white-space: nowrap; padding-bottom: 10px; }
  .jobs-table thead th:hover { color: var(--ink); }
  .jobs-table thead th .sort-icon { display: inline-block; width: 12px; margin-left: 3px; opacity: 0.4; }
  .jobs-table thead th.sort-asc .sort-icon::after { content: '▲'; }
  .jobs-table thead th.sort-desc .sort-icon::after { content: '▼'; }
  .jobs-table thead th.sort-asc .sort-icon, .jobs-table thead th.sort-desc .sort-icon { opacity: 1; color: var(--accent); }
  .jobs-table tbody tr:hover td { background: var(--panel); }
  .jobs-table tbody tr.hidden { display: none; }
  .jobs-table td { overflow-wrap: anywhere; }
  .jobs-table a { color: var(--ink); font-weight: normal; }
  .jobs-table a:hover { color: var(--accent); text-decoration: underline; }
  .score-badge { font-family: var(--mono); font-size: 0.75em; color: var(--accent); }
  .fit-cell .score-badge { display: block; margin-bottom: 2px; }
  .fit-cell span { display: block; margin-top: 2px; color: var(--muted); font-size: 0.82rem; }
  .tags-cell { min-width: 120px; }
  .job-tag { display: inline-block; margin: 0 3px 3px 0; padding: 1px 6px; border: 1px solid var(--line); color: var(--muted); font-size: 0.68rem; font-family: var(--mono); }
  .empty-cell { color: var(--muted); text-align: center; padding: 20px; }

  /* ── Daily page item list ── */
  .day-section { margin-bottom: 1.75rem; }
  .day-section h3 { font-family: var(--mono); font-size: 0.68rem; text-transform: uppercase; letter-spacing: 0.12em; color: var(--faint); margin: 0 0 0.6rem; }
  .day-list { list-style: none; padding: 0; margin: 0; }
  .day-list li { padding: 0.55rem 0; border-bottom: 1px dotted var(--line); }
  .day-list li:last-child { border-bottom: none; }
  .day-item-title { font-size: 0.95rem; line-height: 1.4; }
  .day-item-meta { font-family: var(--mono); font-size: 0.7rem; color: var(--faint); margin: 0.15rem 0 0.25rem; }
  .day-item-summary { font-size: 0.86rem; color: var(--muted); margin: 0; line-height: 1.5; }

  @media (max-width: 700px) {
    body { padding: 1rem 1rem 3rem; }
    .jobs-table, .jobs-table thead, .jobs-table tbody, .jobs-table th, .jobs-table td, .jobs-table tr { display: block; width: 100%; }
    .jobs-table thead { display: none; }
    .jobs-table tbody tr { margin-bottom: 12px; border: 1px solid var(--line); }
    .jobs-table tbody tr.hidden { display: none; }
    .jobs-table td { display: grid; grid-template-columns: 100px 1fr; gap: 6px; padding: 7px 8px; }
    .jobs-table td::before { content: attr(data-label); font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.08em; color: var(--faint); font-family: var(--mono); padding-top: 2px; }
  }
"""

_JS = """
(function() {
  var table = document.getElementById('jobs-table');
  if (!table) return;
  var tbody = table.querySelector('tbody');
  var searchInput = document.getElementById('job-search');
  var clearBtn = document.getElementById('clear-search');
  var countEl = document.getElementById('jobs-visible-count');
  var tagRow = document.getElementById('tag-filter-row');
  var activeTag = '__all__';
  var sortCol = -1, sortDir = 1;

  function updateCount() {
    var rows = tbody.querySelectorAll('tr:not(.hidden)');
    var total = tbody.querySelectorAll('tr').length;
    if (countEl) {
      countEl.textContent = rows.length === total
        ? total + ' positions'
        : rows.length + ' of ' + total + ' positions';
    }
  }

  function filterRows() {
    var query = searchInput ? searchInput.value.toLowerCase().trim() : '';
    var rows = tbody.querySelectorAll('tr');
    rows.forEach(function(row) {
      var text = row.textContent.toLowerCase();
      var rowTags = (row.dataset.tags || '').toLowerCase();
      var matchSearch = !query || text.includes(query);
      var matchTag = activeTag === '__all__' || rowTags.includes(activeTag.toLowerCase());
      row.classList.toggle('hidden', !(matchSearch && matchTag));
    });
    updateCount();
  }

  function sortTable(col) {
    var rows = Array.from(tbody.querySelectorAll('tr'));
    if (!rows.length) return;
    if (sortCol === col) { sortDir *= -1; }
    else { sortCol = col; sortDir = 1; }

    rows.sort(function(a, b) {
      var aCells = a.querySelectorAll('td');
      var bCells = b.querySelectorAll('td');
      var aText = aCells[col] ? aCells[col].textContent.trim() : '';
      var bText = bCells[col] ? bCells[col].textContent.trim() : '';
      // Try numeric comparison for score/date-like columns
      var aNum = parseFloat(aText), bNum = parseFloat(bText);
      if (!isNaN(aNum) && !isNaN(bNum)) return sortDir * (aNum - bNum);
      return sortDir * aText.localeCompare(bText, undefined, {numeric: true});
    });
    rows.forEach(function(r) { tbody.appendChild(r); });

    // Update header indicators
    table.querySelectorAll('thead th').forEach(function(th, i) {
      th.classList.remove('sort-asc', 'sort-desc');
      if (parseInt(th.dataset.col, 10) === col) {
        th.classList.add(sortDir === 1 ? 'sort-asc' : 'sort-desc');
      }
    });
  }

  // Wire up sort on headers
  table.querySelectorAll('thead th[data-col]').forEach(function(th) {
    th.addEventListener('click', function() {
      sortTable(parseInt(th.dataset.col, 10));
    });
  });

  // Wire up search
  if (searchInput) {
    searchInput.addEventListener('input', function() {
      clearBtn && clearBtn.classList.toggle('visible', !!searchInput.value);
      filterRows();
    });
  }
  if (clearBtn) {
    clearBtn.addEventListener('click', function() {
      searchInput.value = '';
      clearBtn.classList.remove('visible');
      filterRows();
    });
  }

  // Wire up tag filters
  if (tagRow) {
    tagRow.addEventListener('click', function(e) {
      var chip = e.target.closest('[data-tag]');
      if (!chip) return;
      activeTag = chip.dataset.tag;
      tagRow.querySelectorAll('.tag-chip').forEach(function(c) {
        c.classList.toggle('active', c === chip);
      });
      filterRows();
    });
  }

  updateCount();
})();
"""


def render_today_briefing(digests: list[dict], jobs: list[dict]) -> str:
    """Render today's top items (news, papers, grants, jobs), excluding repeats from previous days."""
    if not digests:
        return "<p class='empty'>No digest available yet.</p>"

    today = digests[0]
    today_date = today.get("date", "")

    # Collect links and normalised titles seen in all previous digests
    previous_seen: set[str] = set()
    for digest in digests[1:]:
        for key in ("relevant_news", "relevant_articles", "relevant_grants"):
            for item in digest.get(key, []):
                link = str(item.get("link", "")).strip()
                title = str(item.get("title", "")).strip().lower()
                if link:
                    previous_seen.add(link)
                if title:
                    previous_seen.add(title)

    def is_new(item: dict) -> bool:
        link = str(item.get("link", "")).strip()
        title = str(item.get("title", "")).strip().lower()
        return link not in previous_seen and title not in previous_seen

    news_new = [i for i in today.get("relevant_news", []) if is_new(i)][:5]
    articles_new = [i for i in today.get("relevant_articles", []) if is_new(i)][:5]
    # Grants often remain active across multiple daily digests, so today's briefing
    # should reflect the current grant slate even when an item first appeared earlier.
    grants_new = list(today.get("relevant_grants", []))[:5]
    # Jobs: only those first surfaced today
    jobs_new = [j for j in jobs if j.get("first_seen_date") == today_date][:4]
    # Fallback: top-scored jobs if none are new today
    if not jobs_new and jobs:
        jobs_new = sorted(jobs, key=lambda j: float(j.get("student_relevance_score", 0) or 0), reverse=True)[:3]

    def _truncate_summary(text: str, limit: int = 220) -> str:
        text = str(text or "").strip()
        if not text:
            return ""
        if len(text) <= limit:
            return text
        # Prefer stopping at a sentence boundary within the limit
        period = text.rfind(".", 0, limit)
        if period > limit // 3:
            return text[:period + 1].strip()
        return text[:limit].rsplit(" ", 1)[0].strip() + "…"

    def render_briefing_section(label: str, items: list[dict], kind: str) -> str:
        if not items:
            return ""
        parts = [f"<div class='briefing-section'><h3>{html.escape(label)}</h3><ul class='briefing-list'>"]
        for item in items:
            title = _clean(item.get("title", "Untitled"))
            link = str(item.get("link", "")).strip()
            raw_summary = str(item.get("llm_summary") or item.get("summary", "")).strip()
            if kind == "jobs":
                # Only use the scored fit reason — never fall back to summary/description fields
                raw_summary = str(item.get("student_fit_reason") or "").strip()
            summary = _truncate_summary(raw_summary)

            if link:
                title_html = f"<a href='{html.escape(link, quote=True)}' target='_blank' rel='noreferrer'>{title}</a>"
            else:
                title_html = title

            meta_parts: list[str] = []
            if kind == "news":
                source = _clean(item.get("feed", "") or item.get("source", ""))
                if source:
                    meta_parts.append(source)
            elif kind == "papers":
                journal = _clean(item.get("journal_name", "") or item.get("feed", ""))
                try:
                    score_str = f"{int(float(item.get('relevance_score', 0)) * 100)}% fit"
                    meta_parts.append(score_str)
                except Exception:
                    pass
                if journal:
                    meta_parts.append(journal)
            elif kind == "grants":
                deadline = _clean(item.get("deadline", "") or item.get("application_deadline", ""))
                amount = _clean(item.get("amount", ""))
                if deadline:
                    meta_parts.append(f"due {deadline}")
                if amount:
                    meta_parts.append(amount)
            elif kind == "jobs":
                org = _clean(item.get("organization", ""))
                # location from GoodWork scraper may contain full job description after city — truncate hard
                raw_location = str(item.get("location", "") or "").strip()
                location = _truncate_display(raw_location.split("(")[0].strip(), 60)
                if org:
                    meta_parts.append(org)
                if location:
                    meta_parts.append(location)

            meta_html = f"<div class='briefing-item-meta'>{' · '.join(meta_parts)}</div>" if meta_parts else ""
            summary_html = f"<p class='briefing-item-summary'>{html.escape(summary)}</p>" if summary else ""

            parts.append(
                "<li>"
                f"<div class='briefing-item-title'>{title_html}</div>"
                f"{meta_html}"
                f"{summary_html}"
                "</li>"
            )
        parts.append("</ul></div>")
        return "".join(parts)

    has_content = any([news_new, articles_new, grants_new, jobs_new])
    body = (
        "<p class='empty'>Nothing new today — check back tomorrow.</p>"
        if not has_content
        else (
            render_briefing_section("News", news_new, "news")
            + render_briefing_section("Papers", articles_new, "papers")
            + render_briefing_section("Grants", grants_new, "grants")
            + render_briefing_section("Jobs", jobs_new, "jobs")
        )
    )

    return (
        "<section class='briefing' id='today'>"
        f"<h2>{html.escape(today_date)}</h2>"
        + body
        + "</section>"
    )


def render_podcast_sidebar(episode_count: int, feed_url: str) -> str:
    if not feed_url:
        return ""
    ep_label = f"{episode_count} episode{'s' if episode_count != 1 else ''}" if episode_count else "No episodes yet"
    return (
        "<div class='nav-caption'>Podcast</div>"
        "<ul>"
        f"<li><a href='{html.escape(feed_url, quote=True)}'>Junkyard Racoon Radio</a></li>"
        f"<li style='color:#8aab96;font-size:0.8rem;font-family:var(--mono)'>{html.escape(ep_label)}</li>"
        "<li style='color:#8aab96;font-size:0.75rem;'>Add the RSS link to your podcast app</li>"
        "</ul>"
    )


def render_index(state: dict, public_url: str, podcast_feed_url: str = "", podcast_episode_count: int = 0, mastodon_state: dict | None = None, mastodon_instance_url: str = "") -> str:
    digests = state.get("digests", [])
    jobs = state.get("jobs", [])
    latest_date = _clean(digests[0]["date"]) if digests else "unknown"
    all_tags = collect_all_tags(jobs)

    articles = flatten_items(digests, "relevant_articles")
    news = flatten_items(digests, "relevant_news")
    grants = flatten_items(digests, "relevant_grants")

    feed_url = public_url.rstrip("/") + "/feed.xml"

    daily_links = " &middot; ".join(
        f"<a href='{_clean(d.get('date', ''))}.html'>{_clean(d.get('date', ''))}</a>"
        for d in digests
    )

    podcast_nav = ""
    if podcast_feed_url:
        podcast_nav = f" &middot; <a href='{html.escape(podcast_feed_url, quote=True)}'>Podcast RSS</a>"

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Racoon Lab Digest</title>
  <link rel="alternate" type="application/rss+xml" title="Racoon Lab Research Digest" href="{html.escape(feed_url, quote=True)}">
  <style>{_CSS}</style>
</head>
<body>
  <div class="site-header">
    <span class="site-title">Junkyard Racoon &middot; Lab Digest</span>
    <span class="site-date">{latest_date}</span>
  </div>
  <nav class="site-nav">
    <a href="#today">Today</a>
    <a href="#papers">Papers</a>
    <a href="#news">News</a>
    <a href="#grants">Grants</a>
    <a href="#jobs">Jobs</a>
    <a href="{html.escape(feed_url, quote=True)}">RSS</a>
  </nav>
  <hr>

  {render_today_briefing(digests, jobs)}

  <hr>

  <section id="papers" class="archive-section">
    <h2>Papers <span class="count-badge">({len(articles)})</span></h2>
    {render_articles_table(articles)}
  </section>

  <section id="news" class="archive-section">
    <h2>News <span class="count-badge">({len(news)})</span></h2>
    {render_news_table(news)}
  </section>

  <section id="grants" class="archive-section">
    <h2>Grants <span class="count-badge">({len(grants)})</span></h2>
    {render_grants_table(grants)}
  </section>

  <section id="jobs" class="archive-section">
    <h2>Jobs Board <span class="count-badge">({len(jobs)} open)</span></h2>
    <p style="font-size:0.84rem;color:var(--muted);margin:0 0 0.75rem;">Continuously updated from tagged newsletter flow.</p>
    {render_jobs_controls(all_tags)}
    {render_jobs_table(jobs)}
  </section>

  {"<hr><p style='font-size:0.72rem;font-family:var(--mono);color:var(--faint);margin:0;'>Daily pages: " + daily_links + podcast_nav + "</p>" if daily_links else ""}

  <script>{_JS}</script>
</body>
</html>
"""


def _render_day_section(label: str, items: list[dict], kind: str) -> str:
    """Render one category section for the daily page (all items, no dedup)."""
    if not items:
        return ""
    parts = [f"<div class='day-section'><h3>{html.escape(label)}</h3><ul class='day-list'>"]
    for item in items:
        title = _clean(item.get("title", "Untitled"))
        link = str(item.get("link", "")).strip()
        if kind == "jobs":
            raw_summary = str(item.get("student_fit_reason") or "").strip()
        else:
            raw_summary = str(item.get("llm_summary") or item.get("summary", "")).strip()
        # Trim to ~2 sentences, hard cap at 220 chars
        if len(raw_summary) > 220:
            period = raw_summary.rfind(".", 0, 220)
            if period > 73:
                raw_summary = raw_summary[:period + 1].strip()
            else:
                raw_summary = raw_summary[:220].rsplit(" ", 1)[0].strip() + "…"

        if link:
            title_html = f"<a href='{html.escape(link, quote=True)}' target='_blank' rel='noreferrer'>{title}</a>"
        else:
            title_html = title

        meta_parts: list[str] = []
        if kind == "news":
            source = _clean(item.get("feed", "") or item.get("source", ""))
            if source:
                meta_parts.append(source)
        elif kind == "papers":
            journal = _clean(item.get("journal_name", "") or item.get("feed", ""))
            try:
                meta_parts.append(f"{int(float(item.get('relevance_score', 0)) * 100)}% fit")
            except Exception:
                pass
            if journal:
                meta_parts.append(journal)
        elif kind == "grants":
            deadline = _clean(item.get("deadline", "") or item.get("application_deadline", ""))
            amount = _clean(item.get("amount", ""))
            if deadline:
                meta_parts.append(f"due {deadline}")
            if amount:
                meta_parts.append(amount)

        meta_html = f"<div class='day-item-meta'>{' · '.join(meta_parts)}</div>" if meta_parts else ""
        summary_html = f"<p class='day-item-summary'>{html.escape(raw_summary)}</p>" if raw_summary else ""

        parts.append(
            "<li>"
            f"<div class='day-item-title'>{title_html}</div>"
            f"{meta_html}"
            f"{summary_html}"
            "</li>"
        )
    parts.append("</ul></div>")
    return "".join(parts)


def render_daily_page(digest: dict, jobs: list[dict], public_url: str) -> str:
    digest_date = _clean(digest.get("date", "unknown"))
    all_tags = collect_all_tags(jobs)
    news_html = _render_day_section("News", digest.get("relevant_news", []), "news")
    papers_html = _render_day_section("Papers", digest.get("relevant_articles", []), "papers")
    grants_html = _render_day_section("Grants", digest.get("relevant_grants", []), "grants")
    day_content = news_html + papers_html + grants_html or "<p class='empty'>No items for this date.</p>"
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lab Digest {digest_date}</title>
  <style>{_CSS}</style>
</head>
<body>
  <div class="site-header">
    <span class="site-title"><a href="index.html">&larr; Lab Digest</a></span>
    <span class="site-date">{digest_date}</span>
  </div>
  <hr>
  <section class="briefing">
    <h2>{digest_date}</h2>
    {day_content}
  </section>
  <hr>
  <section class="archive-section" id="jobs">
    <h2>Jobs Board <span class="count-badge">({len(jobs)} open)</span></h2>
    {render_jobs_controls(all_tags)}
    {render_jobs_table(jobs)}
  </section>
  <script>{_JS}</script>
</body>
</html>
"""


def _assert_site_dir_writable(site_dir: Path) -> None:
    """Fail fast with an actionable message if the site directory is not writable."""
    site_dir.mkdir(parents=True, exist_ok=True)
    test_file = site_dir / ".write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
    except PermissionError:
        raise SystemExit(
            f"\nERROR: Cannot write to site directory: {site_dir}\n"
            "The directory is owned by a different user. Fix it with:\n\n"
            f"  sudo chown -R $(whoami):$(whoami) {site_dir}\n"
        )


def publish_site(site_dir: Path, deploy_dir: Path | None) -> None:
    if deploy_dir is None:
        return
    deploy_dir.mkdir(parents=True, exist_ok=True)
    for path in site_dir.iterdir():
        target = deploy_dir / path.name
        if path.is_file():
            shutil.copy2(path, target)
        elif path.is_dir():
            shutil.copytree(path, target, dirs_exist_ok=True)


def main() -> None:
    ensure_data_dirs()
    digest = load_json(OUTPUT_DIR / "daily_digest.json", default={})
    if not digest:
        raise SystemExit("No daily digest found. Run processing/daily_digest.py first.")

    output_cfg = load_yaml(CONFIGS_DIR / "output.yaml")
    static_cfg = output_cfg.get("static_site", {})
    public_url = str(static_cfg.get("public_url", "https://lab.tim-a.ca/digest/")).rstrip("/") + "/"
    site_dir = Path(static_cfg.get("site_dir", OUTPUT_DIR / "static_digest_site"))
    deploy_dir_value = str(static_cfg.get("deploy_dir", "")).strip()
    deploy_dir = Path(deploy_dir_value) if deploy_dir_value else None

    _assert_site_dir_writable(site_dir)

    lab_profile = load_yaml(CONFIGS_DIR / "lab_profile.yaml")
    job_threshold = float(lab_profile.get("job_relevance_threshold", 0.80))

    current_date = datetime.date.fromisoformat(digest.get("date", datetime.date.today().isoformat()))
    state = load_state()
    state["digests"] = merge_digest_history(state.get("digests", []), digest, current_date)
    state["jobs"] = merge_jobs(state.get("jobs", []), digest.get("open_jobs", []), current_date)
    # Evict any jobs that fall below the current relevance threshold (catches stale pre-threshold entries)
    state["jobs"] = [j for j in state["jobs"] if float(j.get("student_relevance_score", 0.0)) >= job_threshold]
    state["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_state(state)

    # Copy podcast files into the site (so they're served alongside the digest)
    podcast_state = load_json(PODCAST_STATE_PATH, default={}) or {}
    podcast_episode_count = int(podcast_state.get("episode_count", 0))
    podcast_feed_url = ""
    if PODCAST_SRC_DIR.exists():
        podcast_site_dir = site_dir / "podcast"
        podcast_site_dir.mkdir(parents=True, exist_ok=True)
        for f in PODCAST_SRC_DIR.iterdir():
            if f.is_file():
                dest = podcast_site_dir / f.name
                try:
                    dest.unlink(missing_ok=True)
                    shutil.copy2(f, dest)
                except OSError as exc:
                    print(f"Warning: could not copy podcast file {f.name}: {exc}", file=sys.stderr)
        if (podcast_site_dir / "feed.xml").exists():
            podcast_feed_url = public_url.rstrip("/") + "/podcast/feed.xml"

    mastodon_state = load_json(MASTODON_STATE_PATH, default={}) or {}
    mastodon_instance_url = os.getenv("MASTODON_INSTANCE_URL", "").strip()

    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(
        render_index(state, public_url, podcast_feed_url, podcast_episode_count, mastodon_state, mastodon_instance_url),
        encoding="utf-8",
    )
    (site_dir / "feed.xml").write_text(
        render_rss_feed(state, public_url),
        encoding="utf-8",
    )
    for item in state["digests"]:
        (site_dir / f"{item['date']}.html").write_text(
            render_daily_page(item, state["jobs"], public_url),
            encoding="utf-8",
        )
    dump_json(site_dir / "digest.json", state)
    homepage_feed_src = OUTPUT_DIR / "homepage_feed.json"
    if homepage_feed_src.exists():
        shutil.copy2(homepage_feed_src, site_dir / "homepage_feed.json")
    publish_site(site_dir, deploy_dir)

    result = {
        "updated_at": state["updated_at"],
        "site_dir": str(site_dir),
        "deploy_dir": str(deploy_dir) if deploy_dir else "",
        "public_url": public_url,
        "latest_digest_date": digest.get("date", ""),
        "jobs_count": len(state["jobs"]),
        "digest_count": len(state["digests"]),
    }
    dump_json(OUTPUT_DIR / "static_digest_publish.json", result)
    print(f"Static digest: {public_url}")
    print(f"Static digest files: {site_dir}")
    if deploy_dir:
        print(f"Static digest deployed to: {deploy_dir}")


if __name__ == "__main__":
    main()
