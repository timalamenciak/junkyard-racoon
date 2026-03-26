#!/usr/bin/env python3
"""Render a static HTML digest site with rolling daily digests and a live jobs table."""

from __future__ import annotations

import datetime
import html
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import CONFIGS_DIR, OUTPUT_DIR, STATE_DIR, dump_json, ensure_data_dirs, load_json, load_yaml


STATE_PATH = STATE_DIR / "static_digest_site.json"

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


def render_item_list(items: list[dict], empty_text: str, score_key: str | None = None, action_key: str | None = None) -> str:
    if not items:
        return f"<p class='empty'>{_clean(empty_text)}</p>"
    parts: list[str] = ["<ul class='item-list'>"]
    for item in items:
        title = _clean(item.get("title", "Untitled"))
        link = str(item.get("link", "")).strip()
        summary = _clean(item.get("llm_summary") or item.get("summary", ""))
        meta: list[str] = []
        if score_key and item.get(score_key) is not None:
            try:
                meta.append(f"{int(float(item.get(score_key, 0)) * 100)}% match")
            except Exception:
                pass
        if action_key and item.get(action_key):
            meta.append(_clean(item.get(action_key, "")))
        parts.append("<li>")
        if link:
            parts.append(f"<a href='{html.escape(link, quote=True)}' target='_blank' rel='noreferrer'>{title}</a>")
        else:
            parts.append(f"<span>{title}</span>")
        if meta:
            parts.append(f"<div class='meta'>{' | '.join(meta)}</div>")
        if summary:
            parts.append(f"<p>{summary}</p>")
        parts.append("</li>")
    parts.append("</ul>")
    return "".join(parts)


def render_jobs_table(items: list[dict]) -> str:
    rows = [
        "<table class='jobs-table'><thead><tr><th>Title</th><th>Organization</th><th>Location</th><th>Rate of Pay</th><th>Posted Date</th><th>Deadline</th><th>Student Fit</th><th>Tags</th></tr></thead><tbody>"
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
                fit_parts.append(f"<strong>{html.escape(score)}</strong>")
            if fit_reason:
                fit_parts.append(f"<span>{fit_reason}</span>")
            fit_cell = "".join(fit_parts)
        tags = [str(value).strip() for value in item.get("student_tags", []) if str(value).strip()]
        tag_html = " ".join(f"<span class='job-tag'>{html.escape(tag)}</span>" for tag in tags[:5])
        rows.append(
            "<tr>"
            f"<td data-label='Title'>{title}</td>"
            f"<td data-label='Organization' title='{html.escape(str(item.get('organization', '')), quote=True)}'>{_truncate_display(item.get('organization', ''), 42)}</td>"
            f"<td data-label='Location' title='{html.escape(str(item.get('location', '')), quote=True)}'>{_truncate_display(item.get('location', ''), 34)}</td>"
            f"<td data-label='Rate of Pay' title='{html.escape(str(item.get('pay', '')), quote=True)}'>{_truncate_display(item.get('pay', '') or item.get('pay_normalized', ''), 28)}</td>"
            f"<td data-label='Posted Date'>{_truncate_display(item.get('posted_date', ''), 18)}</td>"
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
    return (
        f"<section id='{digest_date}' class='digest-card'>"
        "<div class='section-heading'>"
        f"<h2>{digest_date}</h2>"
        "<span class='section-tag'>Daily salvage</span>"
        "</div>"
        "<div class='digest-grid'>"
        "<div class='column-card'><h3>News</h3>"
        + render_item_list(digest.get("relevant_news", []), "No relevant news items.", None, None)
        + "</div>"
        "<div class='column-card'><h3>Articles</h3>"
        + render_item_list(digest.get("relevant_articles", []), "No high-relevance papers.", "relevance_score", "recommended_action")
        + "</div>"
        "<div class='column-card'><h3>Grants</h3>"
        + render_item_list(digest.get("relevant_grants", []), "No high-fit grant opportunities.", "relevance_score", "next_step")
        + "</div>"
        "</div>"
        "</section>"
    )


def render_index(state: dict, public_url: str) -> str:
    digests = state.get("digests", [])
    jobs = state.get("jobs", [])
    latest_date = _clean(digests[0]["date"]) if digests else "unknown"
    sidebar_links = ["<li><a href='#jobs'>Open Jobs</a></li>"]
    for digest in digests:
        date_str = _clean(digest.get("date", "unknown"))
        sidebar_links.append(f"<li><a href='#{date_str}'>{date_str}</a></li>")
    archive_links = []
    for digest in digests:
        date_str = _clean(digest.get("date", "unknown"))
        archive_links.append(f"<li><a href='{date_str}.html'>{date_str}</a></li>")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lab Digest</title>
  <style>
    :root {{ --bg:#ede6d8; --bg-deep:#d9cfbe; --panel:#fffaf0; --panel-strong:#f6eedf; --ink:#211f1a; --muted:#655d52; --line:#d4c4aa; --accent:#355f47; --accent-soft:#dce8df; --scrap:#7e5738; --night:#20252b; --sun:#d28f2c; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Georgia, "Times New Roman", serif; background:radial-gradient(circle at top, rgba(210,143,44,0.18), transparent 22%), linear-gradient(180deg, #f5efe2 0%, var(--bg) 48%, var(--bg-deep) 100%); color:var(--ink); }}
    a {{ color:var(--accent); }}
    .layout {{ display:grid; grid-template-columns:300px 1fr; min-height:100vh; }}
    .sidebar {{ position:sticky; top:0; align-self:start; height:100vh; overflow:auto; padding:28px; background:linear-gradient(180deg, #1f2529 0%, #29353c 55%, #355f47 100%); color:#f4efe6; border-right:1px solid rgba(255,255,255,0.08); }}
    .sidebar a {{ color:#f6ead3; text-decoration:none; }}
    .sidebar ul {{ list-style:none; padding:0; margin:12px 0 0; }}
    .sidebar li {{ margin:0 0 10px; }}
    .sidebar .nav-caption {{ color:#d6cab7; text-transform:uppercase; letter-spacing:0.12em; font-size:0.72rem; margin:28px 0 10px; }}
    .content {{ padding:36px; }}
    .hero, .digest-card, .jobs-card {{ background:rgba(255,250,240,0.92); border:1px solid rgba(126,87,56,0.18); border-radius:26px; padding:26px; box-shadow:0 18px 44px rgba(44,31,18,0.08); margin-bottom:24px; backdrop-filter:blur(10px); }}
    .hero {{ position:relative; overflow:hidden; padding:30px; background:linear-gradient(135deg, rgba(255,250,240,0.96), rgba(244,232,214,0.92)); }}
    .hero:after {{ content:""; position:absolute; inset:auto -60px -70px auto; width:240px; height:240px; background:radial-gradient(circle, rgba(53,95,71,0.22), transparent 65%); }}
    .hero-copy {{ max-width:700px; position:relative; z-index:1; }}
    .hero p, .meta, .empty {{ color:var(--muted); }}
    .brand-kicker {{ display:inline-block; font-size:0.78rem; letter-spacing:0.16em; text-transform:uppercase; color:var(--scrap); margin-bottom:10px; }}
    .hero-shell {{ display:flex; gap:28px; align-items:center; justify-content:space-between; }}
    .hero h1 {{ margin:0 0 10px; font-size:clamp(2rem, 4vw, 3.2rem); line-height:1; }}
    .hero-lede {{ font-size:1.05rem; max-width:58ch; }}
    .raccoon-badge {{ width:170px; min-width:170px; filter:drop-shadow(0 16px 22px rgba(33,31,26,0.18)); }}
    .metrics {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; margin-top:22px; position:relative; z-index:1; }}
    .metric-chip {{ background:rgba(255,255,255,0.55); border:1px solid rgba(53,95,71,0.14); border-radius:18px; padding:14px 16px; }}
    .metric-chip span {{ display:block; font-size:0.78rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--muted); }}
    .metric-chip strong {{ display:block; margin-top:6px; font-size:1.5rem; color:var(--night); }}
    .jobs-card h2, .digest-card h2 {{ margin-top:0; }}
    .section-heading {{ display:flex; align-items:center; justify-content:space-between; gap:14px; }}
    .section-tag {{ padding:6px 10px; border-radius:999px; background:var(--accent-soft); color:var(--accent); font-size:0.8rem; }}
    .jobs-table {{ width:100%; border-collapse:separate; border-spacing:0; border-radius:18px; overflow:hidden; table-layout:fixed; }}
    .jobs-table th, .jobs-table td {{ border-bottom:1px solid rgba(126,87,56,0.15); padding:12px 14px; text-align:left; vertical-align:top; }}
    .jobs-table thead th {{ background:linear-gradient(180deg, #e4eadf, #d6e2d9); font-size:0.82rem; letter-spacing:0.08em; text-transform:uppercase; color:#325742; }}
    .jobs-table tbody tr:nth-child(odd) td {{ background:rgba(255,255,255,0.48); }}
    .jobs-table tbody tr:nth-child(even) td {{ background:rgba(246,238,223,0.72); }}
    .jobs-table td {{ overflow-wrap:anywhere; }}
    .fit-cell strong {{ display:block; color:var(--night); }}
    .fit-cell span {{ display:block; margin-top:4px; color:var(--muted); font-size:0.92rem; }}
    .tags-cell {{ min-width:180px; }}
    .job-tag {{ display:inline-block; margin:0 6px 6px 0; padding:5px 9px; border-radius:999px; background:rgba(53,95,71,0.12); color:var(--accent); font-size:0.78rem; line-height:1; }}
    .digest-grid {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:18px; margin-top:16px; }}
    .column-card {{ background:var(--panel-strong); border:1px solid rgba(126,87,56,0.12); border-radius:18px; padding:16px; }}
    .column-card h3 {{ margin:0 0 14px; }}
    .item-list {{ list-style:none; padding:0; margin:0; }}
    .item-list li {{ padding:0 0 14px; margin:0 0 14px; border-bottom:1px solid var(--line); }}
    .item-list li:last-child {{ margin-bottom:0; border-bottom:none; padding-bottom:0; }}
    .item-list a, .jobs-table a {{ font-weight:700; text-decoration:none; }}
    .item-list a:hover, .jobs-table a:hover {{ text-decoration:underline; }}
    .item-list p {{ margin:8px 0 0; color:var(--muted); font-size:0.96rem; }}
    .empty-cell {{ color:var(--muted); text-align:center; }}
    @media (max-width: 1120px) {{ .metrics {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} .hero-shell {{ flex-direction:column; align-items:flex-start; }} .raccoon-badge {{ width:132px; min-width:132px; }} }}
    @media (max-width: 980px) {{ .layout {{ grid-template-columns:1fr; }} .sidebar {{ position:relative; height:auto; }} .digest-grid {{ grid-template-columns:1fr; }} .content {{ padding:18px; }} .metrics {{ grid-template-columns:1fr 1fr; }} }}
    @media (max-width: 760px) {{
      .metrics {{ grid-template-columns:1fr; }}
      .jobs-table, .jobs-table thead, .jobs-table tbody, .jobs-table th, .jobs-table td, .jobs-table tr {{ display:block; width:100%; }}
      .jobs-table thead {{ display:none; }}
      .jobs-table tbody tr {{ margin-bottom:14px; border:1px solid rgba(126,87,56,0.15); border-radius:16px; overflow:hidden; }}
      .jobs-table td {{ display:grid; grid-template-columns:120px 1fr; gap:10px; padding:10px 12px; }}
      .jobs-table td::before {{ content:attr(data-label); font-size:0.78rem; text-transform:uppercase; letter-spacing:0.08em; color:var(--muted); }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand-kicker">Junkyard Racoon</div>
      <h1>Lab Digest</h1>
      <p>Latest salvage run: {latest_date}</p>
      <div class="nav-caption">Jump To</div>
      <ul>{''.join(sidebar_links)}</ul>
      <div class="nav-caption">Daily Pages</div>
      <ul>{''.join(archive_links)}</ul>
    </aside>
    <main class="content">
      <section class="hero">
        <div class="hero-shell">
          <div class="hero-copy">
            <div class="brand-kicker">Field Notes, Salvaged Nightly</div>
            <h1>Daily Research Digest</h1>
            <p class="hero-lede">A cleaner front door for the lab: rolling intelligence on news, articles, grants, and one continuously updated jobs table that stays useful between runs.</p>
            <p>Public URL: <a href="{html.escape(public_url, quote=True)}">{_clean(public_url)}</a></p>
          </div>
          {render_raccoon_badge()}
        </div>
        <div class="metrics">{render_metric_chips(digests, jobs)}</div>
      </section>
      <section id="jobs" class="jobs-card">
        <h2>Open Jobs</h2>
        <p class="meta">Continuously updated from the jobs-tagged newsletter flow. This table is the persistent board; daily digest entries below cover news, grants, and articles.</p>
        {render_jobs_table(jobs)}
      </section>
      {''.join(render_digest_section(digest) for digest in digests)}
    </main>
  </div>
</body>
</html>
"""


def render_daily_page(digest: dict, jobs: list[dict], public_url: str) -> str:
    digest_date = _clean(digest.get("date", "unknown"))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Lab Digest {digest_date}</title>
  <style>
    body {{ max-width:1180px; margin:0 auto; padding:28px; font-family:Georgia, "Times New Roman", serif; background:linear-gradient(180deg, #f7f1e4 0%, #e9dfcc 100%); color:#1f2a1f; }}
    .panel {{ background:#fffaf0; border:1px solid #d4c4aa; border-radius:22px; padding:24px; margin-bottom:20px; box-shadow:0 18px 40px rgba(44,31,18,0.08); }}
    .digest-grid {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:18px; margin-top:14px; }}
    .column-card {{ background:#f6eedf; border:1px solid rgba(126,87,56,0.12); border-radius:18px; padding:16px; }}
    .item-list {{ list-style:none; padding:0; margin:0; }}
    .item-list li {{ padding:0 0 14px; margin:0 0 14px; border-bottom:1px solid #d8d2c5; }}
    .item-list li:last-child {{ border-bottom:none; margin-bottom:0; padding-bottom:0; }}
    .jobs-table {{ width:100%; border-collapse:separate; border-spacing:0; table-layout:fixed; }}
    .jobs-table th, .jobs-table td {{ border-bottom:1px solid #d8d2c5; padding:10px 12px; text-align:left; vertical-align:top; }}
    .jobs-table thead th {{ background:#dce8df; text-transform:uppercase; letter-spacing:0.08em; font-size:0.82rem; color:#355f47; }}
    .jobs-table tbody tr:nth-child(odd) td {{ background:rgba(255,255,255,0.45); }}
    .jobs-table tbody tr:nth-child(even) td {{ background:rgba(246,238,223,0.75); }}
    .jobs-table td {{ overflow-wrap:anywhere; }}
    .fit-cell strong {{ display:block; color:#20252b; }}
    .fit-cell span {{ display:block; margin-top:4px; color:#655d52; font-size:0.92rem; }}
    .job-tag {{ display:inline-block; margin:0 6px 6px 0; padding:5px 9px; border-radius:999px; background:rgba(53,95,71,0.12); color:#355f47; font-size:0.78rem; line-height:1; }}
    .item-list a, .jobs-table a {{ color:#355f47; font-weight:700; text-decoration:none; }}
    .item-list a:hover, .jobs-table a:hover {{ text-decoration:underline; }}
    @media (max-width:980px) {{ .digest-grid {{ grid-template-columns:1fr; }} }}
    @media (max-width:760px) {{
      .jobs-table, .jobs-table thead, .jobs-table tbody, .jobs-table th, .jobs-table td, .jobs-table tr {{ display:block; width:100%; }}
      .jobs-table thead {{ display:none; }}
      .jobs-table tbody tr {{ margin-bottom:14px; border:1px solid rgba(126,87,56,0.15); border-radius:16px; overflow:hidden; }}
      .jobs-table td {{ display:grid; grid-template-columns:120px 1fr; gap:10px; padding:10px 12px; }}
      .jobs-table td::before {{ content:attr(data-label); font-size:0.78rem; text-transform:uppercase; letter-spacing:0.08em; color:#655d52; }}
    }}
  </style>
</head>
<body>
  <div class="panel">
    <p><a href="index.html">Back to digest index</a></p>
    <div style="letter-spacing:.16em;text-transform:uppercase;color:#7e5738;font-size:.78rem;">Junkyard Racoon</div>
    <h1>Daily Digest {digest_date}</h1>
    <p>Base URL: <a href="{html.escape(public_url, quote=True)}">{_clean(public_url)}</a></p>
  </div>
  <div class="panel">
    <h2>Open Jobs</h2>
    {render_jobs_table(jobs)}
  </div>
  {render_digest_section(digest)}
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

    current_date = datetime.date.fromisoformat(digest.get("date", datetime.date.today().isoformat()))
    state = load_state()
    state["digests"] = merge_digest_history(state.get("digests", []), digest, current_date)
    state["jobs"] = merge_jobs(state.get("jobs", []), digest.get("open_jobs", []), current_date)
    state["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_state(state)

    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "index.html").write_text(render_index(state, public_url), encoding="utf-8")
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
