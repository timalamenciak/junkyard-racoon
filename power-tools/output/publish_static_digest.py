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
        "<table class='jobs-table'><thead><tr><th>Title</th><th>Organization</th><th>Location</th><th>Rate of Pay</th><th>Posted Date</th><th>Application Deadline</th></tr></thead><tbody>"
    ]
    if not items:
        rows.append("<tr><td colspan='6' class='empty-cell'>No open positions right now.</td></tr>")
    for item in items:
        title = _clean(item.get("title", "Untitled role"))
        link = str(item.get("link", "")).strip()
        if link:
            title = f"<a href='{html.escape(link, quote=True)}' target='_blank' rel='noreferrer'>{title}</a>"
        rows.append(
            "<tr>"
            f"<td>{title}</td>"
            f"<td>{_clean(item.get('organization', ''))}</td>"
            f"<td>{_clean(item.get('location', ''))}</td>"
            f"<td>{_clean(item.get('pay', ''))}</td>"
            f"<td>{_clean(item.get('posted_date', ''))}</td>"
            f"<td>{_clean(item.get('application_deadline', ''))}</td>"
            "</tr>"
        )
    rows.append("</tbody></table>")
    return "".join(rows)


def render_digest_section(digest: dict) -> str:
    digest_date = _clean(digest.get("date", "unknown"))
    return (
        f"<section id='{digest_date}' class='digest-card'>"
        f"<h2>{digest_date}</h2>"
        "<div class='digest-grid'>"
        "<div><h3>News</h3>"
        + render_item_list(digest.get("relevant_news", []), "No relevant news items.", None, None)
        + "</div>"
        "<div><h3>Articles</h3>"
        + render_item_list(digest.get("relevant_articles", []), "No high-relevance papers.", "relevance_score", "recommended_action")
        + "</div>"
        "<div><h3>Grants</h3>"
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
    :root {{ --bg:#f4f1ea; --panel:#fffdf8; --ink:#1f2a1f; --muted:#5d6b61; --line:#d8d2c5; --accent:#245c45; --accent-soft:#dceee5; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:Georgia, "Times New Roman", serif; background:linear-gradient(180deg, #f8f5ef 0%, var(--bg) 100%); color:var(--ink); }}
    a {{ color:var(--accent); }}
    .layout {{ display:grid; grid-template-columns:280px 1fr; min-height:100vh; }}
    .sidebar {{ position:sticky; top:0; align-self:start; height:100vh; overflow:auto; padding:24px; background:#1f3127; color:#eef4ef; }}
    .sidebar a {{ color:#eef4ef; text-decoration:none; }}
    .sidebar ul {{ list-style:none; padding:0; margin:12px 0 0; }}
    .sidebar li {{ margin:0 0 10px; }}
    .content {{ padding:32px; }}
    .hero, .digest-card, .jobs-card {{ background:var(--panel); border:1px solid var(--line); border-radius:20px; padding:24px; box-shadow:0 12px 30px rgba(31,42,31,0.06); margin-bottom:24px; }}
    .hero p, .meta, .empty {{ color:var(--muted); }}
    .jobs-card h2, .digest-card h2 {{ margin-top:0; }}
    .jobs-table {{ width:100%; border-collapse:collapse; font-size:0.96rem; }}
    .jobs-table th, .jobs-table td {{ border-bottom:1px solid var(--line); padding:10px 12px; text-align:left; vertical-align:top; }}
    .jobs-table thead th {{ background:var(--accent-soft); }}
    .digest-grid {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:18px; }}
    .item-list {{ list-style:none; padding:0; margin:0; }}
    .item-list li {{ padding:0 0 14px; margin:0 0 14px; border-bottom:1px solid var(--line); }}
    .item-list li:last-child {{ margin-bottom:0; border-bottom:none; padding-bottom:0; }}
    .item-list p {{ margin:8px 0 0; color:var(--muted); }}
    .empty-cell {{ color:var(--muted); text-align:center; }}
    @media (max-width: 980px) {{ .layout {{ grid-template-columns:1fr; }} .sidebar {{ position:relative; height:auto; }} .digest-grid {{ grid-template-columns:1fr; }} .content {{ padding:18px; }} }}
  </style>
</head>
<body>
  <div class="layout">
    <aside class="sidebar">
      <h1>Lab Digest</h1>
      <p>Latest update: {latest_date}</p>
      <ul>{''.join(sidebar_links)}</ul>
      <h2>Daily Pages</h2>
      <ul>{''.join(archive_links)}</ul>
    </aside>
    <main class="content">
      <section class="hero">
        <h1>Daily Research Digest</h1>
        <p>Public URL: <a href="{html.escape(public_url, quote=True)}">{_clean(public_url)}</a></p>
        <p>This page appends recent daily digests and keeps one continuously updated jobs table at the top.</p>
      </section>
      <section id="jobs" class="jobs-card">
        <h2>Open Jobs</h2>
        <p class="meta">Continuously updated from the jobs-tagged newsletter flow.</p>
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
    body {{ max-width: 1100px; margin: 0 auto; padding: 24px; font-family: Georgia, "Times New Roman", serif; background:#f8f5ef; color:#1f2a1f; }}
    .panel {{ background:#fffdf8; border:1px solid #d8d2c5; border-radius:18px; padding:24px; margin-bottom:20px; }}
    .digest-grid {{ display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:18px; }}
    .item-list {{ list-style:none; padding:0; margin:0; }}
    .item-list li {{ padding:0 0 14px; margin:0 0 14px; border-bottom:1px solid #d8d2c5; }}
    .item-list li:last-child {{ border-bottom:none; margin-bottom:0; padding-bottom:0; }}
    .jobs-table {{ width:100%; border-collapse:collapse; }}
    .jobs-table th, .jobs-table td {{ border-bottom:1px solid #d8d2c5; padding:10px 12px; text-align:left; vertical-align:top; }}
    @media (max-width: 980px) {{ .digest-grid {{ grid-template-columns:1fr; }} }}
  </style>
</head>
<body>
  <div class="panel">
    <p><a href="index.html">Back to digest index</a></p>
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
