#!/usr/bin/env python3
"""Render a static HTML digest site with rolling daily digests and a live jobs table."""

from __future__ import annotations

import datetime
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
        parts.append("<li>")
        parts.append(title_html)
        if meta:
            parts.append(f"<div class='item-meta'>{' &middot; '.join(meta)}</div>")
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
    --bg:#ede6d8; --bg-deep:#d9cfbe; --panel:#fffaf0; --panel-strong:#f6eedf;
    --ink:#211f1a; --muted:#655d52; --line:#d4c4aa;
    --accent:#2d5c40; --accent-soft:#dce8df; --accent-glow:rgba(45,92,64,0.22);
    --scrap:#7e5738; --night:#1a1f24; --sun:#d28f2c;
    --mono:'JetBrains Mono','Fira Mono','Courier New',monospace;
    --radius:20px;
  }
  *{box-sizing:border-box;}
  body{margin:0;font-family:Georgia,"Times New Roman",serif;background:radial-gradient(circle at top,rgba(210,143,44,0.14),transparent 22%),linear-gradient(180deg,#f5efe2 0%,var(--bg) 48%,var(--bg-deep) 100%);color:var(--ink);}
  a{color:var(--accent);}
  code{font-family:var(--mono);font-size:0.82em;background:rgba(45,92,64,0.1);border:1px solid rgba(45,92,64,0.18);border-radius:5px;padding:1px 5px;color:var(--accent);}

  /* ── Layout ── */
  .layout{display:grid;grid-template-columns:280px 1fr;min-height:100vh;}
  .sidebar{position:sticky;top:0;align-self:start;height:100vh;overflow:auto;padding:24px 20px;
    background:linear-gradient(180deg,#181d21 0%,#222c33 55%,#2d5c40 100%);
    color:#f4efe6;border-right:1px solid rgba(255,255,255,0.06);}
  .sidebar a{color:#f6ead3;text-decoration:none;}
  .sidebar a:hover{color:#a3e4b0;}
  .sidebar ul{list-style:none;padding:0;margin:10px 0 0;}
  .sidebar li{margin:0 0 8px;}
  .sidebar .nav-caption{color:#8aab96;text-transform:uppercase;letter-spacing:0.12em;font-size:0.68rem;margin:24px 0 8px;font-family:var(--mono);}
  .sidebar-brand{font-family:var(--mono);font-size:0.72rem;letter-spacing:0.2em;text-transform:uppercase;color:#5ec47a;margin-bottom:6px;}
  .sidebar h1{margin:0 0 6px;font-size:1.4rem;color:#f4efe6;}
  .sidebar-meta{font-size:0.8rem;color:#8aab96;font-family:var(--mono);}
  .content{padding:36px;}

  /* ── Cards ── */
  .hero,.digest-card,.jobs-card{background:rgba(255,250,240,0.92);border:1px solid rgba(126,87,56,0.18);border-radius:var(--radius);padding:26px;box-shadow:0 18px 44px rgba(44,31,18,0.08);margin-bottom:24px;backdrop-filter:blur(10px);}
  .hero{position:relative;overflow:hidden;padding:30px;background:linear-gradient(135deg,rgba(255,250,240,0.96),rgba(244,232,214,0.92));}
  .hero:after{content:"";position:absolute;inset:auto -60px -70px auto;width:240px;height:240px;background:radial-gradient(circle,var(--accent-glow),transparent 65%);}
  .hero-copy{max-width:700px;position:relative;z-index:1;}
  .hero p,.meta,.empty{color:var(--muted);}
  .brand-kicker{display:inline-block;font-size:0.72rem;letter-spacing:0.18em;text-transform:uppercase;color:var(--scrap);margin-bottom:10px;font-family:var(--mono);}
  .hero-shell{display:flex;gap:28px;align-items:center;justify-content:space-between;}
  .hero h1{margin:0 0 10px;font-size:clamp(2rem,4vw,3rem);line-height:1.1;}
  .hero-lede{font-size:1rem;max-width:58ch;}
  .raccoon-badge{width:160px;min-width:160px;filter:drop-shadow(0 16px 22px rgba(33,31,26,0.18));}

  /* ── Metric chips ── */
  .metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:22px;position:relative;z-index:1;}
  .metric-chip{background:rgba(255,255,255,0.55);border:1px solid rgba(45,92,64,0.14);border-radius:14px;padding:14px 16px;}
  .metric-chip span{display:block;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.1em;color:var(--muted);font-family:var(--mono);}
  .metric-chip strong{display:block;margin-top:6px;font-size:1.5rem;color:var(--night);font-family:var(--mono);}

  /* ── Section headings ── */
  .jobs-card h2,.digest-card h2{margin-top:0;}
  .section-heading{display:flex;align-items:center;justify-content:space-between;gap:14px;}
  .section-tag{padding:5px 10px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:0.75rem;font-family:var(--mono);letter-spacing:0.06em;}

  /* ── Jobs controls ── */
  .jobs-controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:16px 0 14px;}
  .search-wrap{position:relative;flex:1;min-width:200px;max-width:360px;}
  .search-icon{position:absolute;left:11px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:1rem;pointer-events:none;}
  .job-search{width:100%;padding:9px 32px 9px 34px;border:1px solid var(--line);border-radius:999px;background:#fff;font-size:0.9rem;font-family:var(--mono);color:var(--ink);outline:none;transition:border-color 0.2s,box-shadow 0.2s;}
  .job-search:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow);}
  .clear-search{position:absolute;right:10px;top:50%;transform:translateY(-50%);background:none;border:none;color:var(--muted);cursor:pointer;font-size:0.85rem;padding:2px 4px;line-height:1;display:none;}
  .clear-search.visible{display:block;}
  .tag-filter-row{display:flex;flex-wrap:wrap;gap:6px;}
  .tag-chip{display:inline-block;padding:5px 11px;border-radius:999px;background:rgba(45,92,64,0.08);border:1px solid rgba(45,92,64,0.2);color:var(--accent);font-size:0.75rem;font-family:var(--mono);cursor:pointer;transition:background 0.15s,border-color 0.15s;line-height:1;}
  .tag-chip:hover{background:rgba(45,92,64,0.16);}
  .tag-chip.active{background:var(--accent);border-color:var(--accent);color:#fff;}
  .jobs-count{font-size:0.78rem;font-family:var(--mono);color:var(--muted);align-self:center;}

  /* ── Jobs table ── */
  .jobs-table{width:100%;border-collapse:separate;border-spacing:0;border-radius:16px;overflow:hidden;table-layout:fixed;}
  .jobs-table th,.jobs-table td{border-bottom:1px solid rgba(126,87,56,0.15);padding:11px 13px;text-align:left;vertical-align:top;}
  .jobs-table thead th{background:linear-gradient(180deg,#e0ead9,#d2e0d3);font-size:0.76rem;letter-spacing:0.08em;text-transform:uppercase;color:#2d5c40;font-family:var(--mono);cursor:pointer;user-select:none;white-space:nowrap;}
  .jobs-table thead th:hover{background:linear-gradient(180deg,#d2e0d3,#c5d9c7);}
  .jobs-table thead th .sort-icon{display:inline-block;width:12px;margin-left:4px;opacity:0.4;}
  .jobs-table thead th.sort-asc .sort-icon::after{content:'▲';}
  .jobs-table thead th.sort-desc .sort-icon::after{content:'▼';}
  .jobs-table thead th.sort-asc .sort-icon,.jobs-table thead th.sort-desc .sort-icon{opacity:1;color:var(--accent);}
  .jobs-table tbody tr:nth-child(odd) td{background:rgba(255,255,255,0.48);}
  .jobs-table tbody tr:nth-child(even) td{background:rgba(246,238,223,0.72);}
  .jobs-table tbody tr:hover td{background:rgba(45,92,64,0.07);}
  .jobs-table tbody tr.hidden{display:none;}
  .jobs-table td{overflow-wrap:anywhere;}
  .score-badge{font-family:var(--mono);font-size:0.8em;background:rgba(45,92,64,0.12);border:1px solid rgba(45,92,64,0.22);border-radius:5px;padding:2px 6px;color:var(--accent);}
  .fit-cell .score-badge{display:block;margin-bottom:4px;}
  .fit-cell span{display:block;margin-top:2px;color:var(--muted);font-size:0.88rem;}
  .tags-cell{min-width:160px;}
  .job-tag{display:inline-block;margin:0 4px 4px 0;padding:3px 8px;border-radius:999px;background:rgba(45,92,64,0.1);color:var(--accent);font-size:0.72rem;font-family:var(--mono);line-height:1;border:1px solid rgba(45,92,64,0.15);}
  .empty-cell{color:var(--muted);text-align:center;padding:24px;}

  /* ── Digest grid ── */
  .digest-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;margin-top:16px;border-top:1px solid var(--line);}
  .column-card{padding:16px 20px;border-right:1px solid var(--line);}
  .column-card:last-child{border-right:none;}
  .column-card h3{margin:0 0 10px;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.1em;color:var(--muted);font-family:var(--mono);}
  .item-list{list-style:disc;padding:0 0 0 18px;margin:0;}
  .item-list li{padding:2px 0 4px;margin:0;line-height:1.45;color:var(--ink);}
  .item-list a{color:var(--ink);font-weight:normal;text-decoration:none;}
  .item-list a:hover{color:var(--accent);text-decoration:underline;}
  .jobs-table a{font-weight:700;text-decoration:none;}
  .jobs-table a:hover{text-decoration:underline;}
  .item-meta{font-size:0.78rem;color:var(--muted);margin:1px 0 4px;line-height:1.3;}

  /* ── Responsive ── */
  @media(max-width:1120px){
    .metrics{grid-template-columns:repeat(2,minmax(0,1fr));}
    .hero-shell{flex-direction:column;align-items:flex-start;}
    .raccoon-badge{width:120px;min-width:120px;}
  }
  @media(max-width:980px){
    .layout{grid-template-columns:1fr;}
    .sidebar{position:relative;height:auto;}
    .digest-grid{grid-template-columns:1fr;}
    .column-card{border-right:none;border-bottom:1px solid var(--line);}
    .column-card:last-child{border-bottom:none;}
    .content{padding:16px;}
    .metrics{grid-template-columns:1fr 1fr;}
  }
  @media(max-width:760px){
    .metrics{grid-template-columns:1fr;}
    .jobs-controls{flex-direction:column;align-items:stretch;}
    .search-wrap{max-width:100%;}
    .jobs-table,.jobs-table thead,.jobs-table tbody,.jobs-table th,.jobs-table td,.jobs-table tr{display:block;width:100%;}
    .jobs-table thead{display:none;}
    .jobs-table tbody tr{margin-bottom:14px;border:1px solid rgba(126,87,56,0.18);border-radius:14px;overflow:hidden;}
    .jobs-table tbody tr.hidden{display:none;}
    .jobs-table td{display:grid;grid-template-columns:110px 1fr;gap:8px;padding:10px 12px;}
    .jobs-table td::before{content:attr(data-label);font-size:0.72rem;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);font-family:var(--mono);padding-top:2px;}
    .todo-list{flex-direction:column;}
    .todo-item{flex:1 1 auto;}
  }

  /* ── Todo list ── */
  .todos-section{margin-top:20px;border-top:1px solid var(--line);padding-top:18px;}
  .todos-section h3{margin:0 0 14px;font-size:1rem;}
  .todo-list{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:10px;}
  .todo-item{background:var(--panel-strong);border:1px solid rgba(126,87,56,0.12);border-radius:12px;padding:10px 14px;display:flex;flex-wrap:wrap;align-items:baseline;gap:6px;flex:1 1 280px;}
  .todo-badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:0.68rem;font-family:var(--mono);letter-spacing:0.07em;text-transform:uppercase;line-height:1.5;flex-shrink:0;}
  .todo-high{background:rgba(210,143,44,0.18);color:#7a4c0a;border:1px solid rgba(210,143,44,0.3);}
  .todo-urgent{background:rgba(180,40,40,0.12);color:#8b1a1a;border:1px solid rgba(180,40,40,0.22);}
  .todo-medium{background:rgba(45,92,64,0.1);color:var(--accent);border:1px solid rgba(45,92,64,0.2);}
  .todo-low{background:rgba(100,100,100,0.07);color:var(--muted);border:1px solid rgba(100,100,100,0.14);}
  .todo-task{font-size:0.93rem;flex:1 1 200px;}
  .todo-project{font-size:0.74rem;font-family:var(--mono);color:var(--muted);background:rgba(0,0,0,0.05);padding:2px 7px;border-radius:6px;flex-shrink:0;}
  .todo-note{margin:4px 0 0;font-size:0.82rem;color:var(--muted);width:100%;}

  /* ── Content card (generic section wrapper) ── */
  .content-card{background:rgba(255,250,240,0.92);border:1px solid rgba(126,87,56,0.18);border-radius:var(--radius);padding:26px;box-shadow:0 18px 44px rgba(44,31,18,0.08);margin-bottom:24px;}

  /* ── Digest tables (news / articles / grants) ── */
  .digest-table{width:100%;border-collapse:collapse;font-size:0.92rem;}
  .digest-table th{text-align:left;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);font-family:var(--mono);font-weight:normal;padding:6px 10px 8px;border-bottom:2px solid var(--line);}
  .digest-table td{padding:7px 10px;border-bottom:1px solid rgba(212,196,170,0.4);vertical-align:top;}
  .digest-table tbody tr:hover td{background:rgba(45,92,64,0.04);}
  .digest-table a{color:var(--ink);text-decoration:none;}
  .digest-table a:hover{color:var(--accent);text-decoration:underline;}
  .date-cell{white-space:nowrap;color:var(--muted);font-size:0.8rem;font-family:var(--mono);width:100px;}
  .muted-cell{color:var(--muted);font-size:0.85rem;}

  /* ── Hover tooltip ── */
  .tip{position:relative;display:inline;}
  .tip .tip-text{display:none;position:absolute;left:0;top:calc(100% + 4px);background:#23262b;color:#f4efe6;padding:8px 12px;border-radius:8px;font-size:0.8rem;line-height:1.5;width:320px;max-width:80vw;white-space:normal;z-index:200;pointer-events:none;box-shadow:0 4px 18px rgba(0,0,0,0.3);}
  .tip:hover .tip-text{display:block;}

  /* ── Mastodon toots ── */
  .toot-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px;margin-top:16px;}
  .toot-card{background:var(--panel-strong);border:1px solid rgba(126,87,56,0.12);border-radius:14px;padding:14px 16px;font-size:0.9rem;line-height:1.55;}
  .toot-card p{margin:0 0 8px;}
  .toot-link{font-size:0.75rem;font-family:var(--mono);color:var(--muted);text-decoration:none;}
  .toot-link:hover{color:var(--accent);}

  /* ── Manual grant badges ── */
  .manual-grant-marker{font-size:0.7rem;font-family:var(--mono);color:var(--scrap);margin-right:4px;}
  .grant-status,.grant-deadline,.grant-amount{display:inline-block;font-size:0.72rem;font-family:var(--mono);border-radius:999px;padding:2px 8px;line-height:1.5;}
  .grant-deadline{background:rgba(210,143,44,0.12);color:#7a4c0a;border:1px solid rgba(210,143,44,0.25);}
  .grant-amount{background:rgba(45,92,64,0.08);color:var(--accent);border:1px solid rgba(45,92,64,0.18);}
  .status-tracking{background:rgba(100,120,200,0.1);color:#2a3a8a;border:1px solid rgba(100,120,200,0.25);}
  .status-drafting{background:rgba(210,143,44,0.14);color:#7a4c0a;border:1px solid rgba(210,143,44,0.28);}
  .status-submitted{background:rgba(45,92,64,0.14);color:var(--accent);border:1px solid rgba(45,92,64,0.25);}
  .status-awarded{background:rgba(45,150,64,0.15);color:#1a5c28;border:1px solid rgba(45,150,64,0.3);}
  .status-declined{background:rgba(180,40,40,0.1);color:#8b1a1a;border:1px solid rgba(180,40,40,0.2);}
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

    archive_links = []
    for digest in digests:
        date_str = _clean(digest.get("date", "unknown"))
        archive_links.append(f"<li><a href='{date_str}.html'>{date_str}</a></li>")

    toots_html = render_toots_section(mastodon_state or {}, mastodon_instance_url)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lab Digest</title>
  <style>{_CSS}</style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="sidebar-brand">Junkyard Racoon</div>
      <h1>Lab Digest</h1>
      <p class="sidebar-meta">Last run: {latest_date}</p>
      <div class="nav-caption">Jump To</div>
      <ul>
        <li><a href="#toots">Latest Toots</a></li>
        <li><a href="#articles">Articles</a></li>
        <li><a href="#news">News</a></li>
        <li><a href="#grants">Grants</a></li>
        <li><a href="#jobs">Open Jobs</a></li>
      </ul>
      <div class="nav-caption">Daily Pages</div>
      <ul>{''.join(archive_links)}</ul>
      {render_podcast_sidebar(podcast_episode_count, podcast_feed_url)}
    </aside>
    <main class="content">
      <section class="hero">
        <div class="hero-shell">
          <div class="hero-copy">
            <div class="brand-kicker">Field Notes, Salvaged Nightly</div>
            <h1>Daily Research Digest</h1>
            <p class="hero-lede">Rolling intelligence on news, articles, grants, and a continuously updated jobs board. Filtered for relevance. Built for the Racoon Lab.</p>
          </div>
          {render_raccoon_badge()}
        </div>
        <div class="metrics">{render_metric_chips(digests, jobs)}</div>
      </section>
      {toots_html}
      <section id="articles" class="content-card">
        <div class="section-heading">
          <h2>Articles</h2>
          <span class="section-tag">{len(articles)} papers</span>
        </div>
        {render_articles_table(articles)}
      </section>
      <section id="news" class="content-card">
        <div class="section-heading">
          <h2>News</h2>
          <span class="section-tag">{len(news)} items</span>
        </div>
        {render_news_table(news)}
      </section>
      <section id="grants" class="content-card">
        <div class="section-heading">
          <h2>Grants</h2>
          <span class="section-tag">{len(grants)} opportunities</span>
        </div>
        {render_grants_table(grants)}
      </section>
      <section id="jobs" class="jobs-card">
        <div class="section-heading">
          <h2>Open Jobs</h2>
          <span class="section-tag">live board</span>
        </div>
        <p class="meta">Continuously updated from the jobs-tagged newsletter flow. Filter and sort to find the right fit.</p>
        {render_jobs_controls(all_tags)}
        {render_jobs_table(jobs)}
      </section>
    </main>
  </div>
  <script>{_JS}</script>
</body>
</html>
"""


def render_daily_page(digest: dict, jobs: list[dict], public_url: str) -> str:
    digest_date = _clean(digest.get("date", "unknown"))
    all_tags = collect_all_tags(jobs)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lab Digest {digest_date}</title>
  <style>
    :root{{--accent:#2d5c40;--accent-soft:#dce8df;--accent-glow:rgba(45,92,64,0.2);--muted:#655d52;--line:#d8d2c5;--ink:#1f2a1f;--mono:'JetBrains Mono','Fira Mono','Courier New',monospace;}}
    *{{box-sizing:border-box;}}
    body{{max-width:1200px;margin:0 auto;padding:24px;font-family:Georgia,"Times New Roman",serif;background:linear-gradient(180deg,#f7f1e4 0%,#e9dfcc 100%);color:var(--ink);}}
    a{{color:var(--accent);}}
    code{{font-family:var(--mono);font-size:0.82em;background:rgba(45,92,64,0.1);border:1px solid rgba(45,92,64,0.18);border-radius:5px;padding:1px 5px;color:var(--accent);}}
    .panel{{background:#fffaf0;border:1px solid #d4c4aa;border-radius:20px;padding:24px;margin-bottom:20px;box-shadow:0 18px 40px rgba(44,31,18,0.08);}}
    .brand-kicker{{font-family:var(--mono);font-size:0.7rem;letter-spacing:0.2em;text-transform:uppercase;color:#7e5738;}}
    .digest-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;margin-top:14px;border-top:1px solid var(--line);}}
    .column-card{{padding:16px 20px;border-right:1px solid var(--line);}}
    .column-card:last-child{{border-right:none;}}
    .column-card h3{{margin:0 0 10px;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.1em;color:var(--muted);font-family:var(--mono);}}
    .item-list{{list-style:disc;padding:0 0 0 18px;margin:0;}}
    .item-list li{{padding:2px 0 4px;margin:0;line-height:1.45;color:var(--ink);}}
    .item-list a{{color:var(--ink);font-weight:normal;text-decoration:none;}}
    .item-list a:hover{{color:var(--accent);text-decoration:underline;}}
    .item-meta{{font-size:0.78rem;color:var(--muted);margin:1px 0 4px;line-height:1.3;}}
    .todos-section{{margin-top:20px;border-top:1px solid var(--line);padding-top:18px;}}
    .todos-section h3{{margin:0 0 14px;font-size:1rem;}}
    .todo-list{{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:10px;}}
    .todo-item{{background:#f6eedf;border:1px solid rgba(126,87,56,0.12);border-radius:12px;padding:10px 14px;display:flex;flex-wrap:wrap;align-items:baseline;gap:6px;flex:1 1 280px;}}
    .todo-badge{{display:inline-block;padding:2px 8px;border-radius:999px;font-size:0.68rem;font-family:var(--mono);letter-spacing:0.07em;text-transform:uppercase;line-height:1.5;flex-shrink:0;}}
    .todo-high{{background:rgba(210,143,44,0.18);color:#7a4c0a;border:1px solid rgba(210,143,44,0.3);}}
    .todo-urgent{{background:rgba(180,40,40,0.12);color:#8b1a1a;border:1px solid rgba(180,40,40,0.22);}}
    .todo-medium{{background:rgba(45,92,64,0.1);color:var(--accent);border:1px solid rgba(45,92,64,0.2);}}
    .todo-low{{background:rgba(100,100,100,0.07);color:var(--muted);border:1px solid rgba(100,100,100,0.14);}}
    .todo-task{{font-size:0.93rem;flex:1 1 200px;}}
    .todo-project{{font-size:0.74rem;font-family:var(--mono);color:var(--muted);background:rgba(0,0,0,0.05);padding:2px 7px;border-radius:6px;flex-shrink:0;}}
    .todo-note{{margin:4px 0 0;font-size:0.82rem;color:var(--muted);width:100%;}}
    .meta{{color:var(--muted);font-size:0.9rem;}}
    .section-heading{{display:flex;align-items:center;justify-content:space-between;gap:12px;}}
    .section-tag{{padding:5px 10px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:0.74rem;font-family:var(--mono);}}
    .jobs-controls{{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:14px 0;}}
    .search-wrap{{position:relative;flex:1;min-width:180px;max-width:340px;}}
    .search-icon{{position:absolute;left:11px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:1rem;pointer-events:none;}}
    .job-search{{width:100%;padding:9px 32px 9px 34px;border:1px solid #d4c4aa;border-radius:999px;background:#fff;font-size:0.88rem;font-family:var(--mono);color:var(--ink);outline:none;transition:border-color 0.2s;}}
    .job-search:focus{{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow);}}
    .clear-search{{position:absolute;right:10px;top:50%;transform:translateY(-50%);background:none;border:none;color:var(--muted);cursor:pointer;font-size:0.85rem;padding:2px 4px;line-height:1;display:none;}}
    .clear-search.visible{{display:block;}}
    .tag-filter-row{{display:flex;flex-wrap:wrap;gap:6px;}}
    .tag-chip{{display:inline-block;padding:5px 11px;border-radius:999px;background:rgba(45,92,64,0.08);border:1px solid rgba(45,92,64,0.2);color:var(--accent);font-size:0.74rem;font-family:var(--mono);cursor:pointer;transition:background 0.15s;line-height:1;}}
    .tag-chip:hover{{background:rgba(45,92,64,0.16);}}
    .tag-chip.active{{background:var(--accent);border-color:var(--accent);color:#fff;}}
    .jobs-count{{font-size:0.76rem;font-family:var(--mono);color:var(--muted);align-self:center;}}
    .jobs-table{{width:100%;border-collapse:separate;border-spacing:0;table-layout:fixed;border-radius:14px;overflow:hidden;}}
    .jobs-table th,.jobs-table td{{border-bottom:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top;}}
    .jobs-table thead th{{background:#dce8df;text-transform:uppercase;letter-spacing:0.08em;font-size:0.74rem;color:var(--accent);font-family:var(--mono);cursor:pointer;user-select:none;white-space:nowrap;}}
    .jobs-table thead th:hover{{background:#cddfd0;}}
    .jobs-table thead th .sort-icon{{display:inline-block;width:12px;margin-left:4px;opacity:0.4;}}
    .jobs-table thead th.sort-asc .sort-icon::after{{content:'▲';}}
    .jobs-table thead th.sort-desc .sort-icon::after{{content:'▼';}}
    .jobs-table thead th.sort-asc .sort-icon,.jobs-table thead th.sort-desc .sort-icon{{opacity:1;}}
    .jobs-table tbody tr:nth-child(odd) td{{background:rgba(255,255,255,0.45);}}
    .jobs-table tbody tr:nth-child(even) td{{background:rgba(246,238,223,0.75);}}
    .jobs-table tbody tr:hover td{{background:rgba(45,92,64,0.07);}}
    .jobs-table tbody tr.hidden{{display:none;}}
    .jobs-table td{{overflow-wrap:anywhere;}}
    .score-badge{{font-family:var(--mono);font-size:0.8em;background:rgba(45,92,64,0.1);border:1px solid rgba(45,92,64,0.2);border-radius:4px;padding:2px 5px;color:var(--accent);display:block;margin-bottom:3px;}}
    .fit-cell span{{display:block;color:var(--muted);font-size:0.88rem;}}
    .job-tag{{display:inline-block;margin:0 4px 4px 0;padding:3px 8px;border-radius:999px;background:rgba(45,92,64,0.1);color:var(--accent);font-size:0.72rem;font-family:var(--mono);line-height:1;}}
    .empty-cell{{color:var(--muted);text-align:center;padding:20px;}}
    @media(max-width:980px){{.digest-grid{{grid-template-columns:1fr;}}}}
    @media(max-width:760px){{
      .jobs-controls{{flex-direction:column;align-items:stretch;}}
      .search-wrap{{max-width:100%;}}
      .jobs-table,.jobs-table thead,.jobs-table tbody,.jobs-table th,.jobs-table td,.jobs-table tr{{display:block;width:100%;}}
      .jobs-table thead{{display:none;}}
      .jobs-table tbody tr{{margin-bottom:12px;border:1px solid rgba(126,87,56,0.18);border-radius:12px;overflow:hidden;}}
      .jobs-table tbody tr.hidden{{display:none;}}
      .jobs-table td{{display:grid;grid-template-columns:100px 1fr;gap:8px;padding:9px 11px;}}
      .jobs-table td::before{{content:attr(data-label);font-size:0.7rem;text-transform:uppercase;letter-spacing:0.08em;color:var(--muted);font-family:var(--mono);padding-top:2px;}}
      .todo-list{{flex-direction:column;}}
      .todo-item{{flex:1 1 auto;}}
    }}
  </style>
</head>
<body>
  <div class="panel">
    <p><a href="index.html">&larr; Back to digest index</a></p>
    <div class="brand-kicker">Junkyard Racoon</div>
    <h1>Daily Digest {digest_date}</h1>
    <p>Base URL: <a href="{html.escape(public_url, quote=True)}">{_clean(public_url)}</a></p>
  </div>
  {render_digest_section(digest)}
  <div class="panel">
    <div class="section-heading"><h2>Open Jobs</h2><span class="section-tag">live board</span></div>
    {render_jobs_controls(all_tags)}
    {render_jobs_table(jobs)}
  </div>
  <script>{_JS}</script>
</body>
</html>
"""


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
    for item in state["digests"]:
        (site_dir / f"{item['date']}.html").write_text(
            render_daily_page(item, state["jobs"], public_url),
            encoding="utf-8",
        )
    dump_json(site_dir / "digest.json", state)
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
