#!/usr/bin/env python3
"""
Check Gmail IMAP for podcast request emails and generate Junkyard Racoon Radio episodes.

Looks for emails from tim.alamenciak@gmail.com with "podcast" in the subject and a
PDF attachment. For each new email found, generates a numbered podcast episode using
podcast_gemini_tts_ver2.py and updates the RSS feed.

Episode state is stored in data/state/podcast_state.json.
Audio, transcripts, notes, and feed.xml are written to data/output/podcast/.
The publish_static_digest step copies that directory into the served site.
"""

from __future__ import annotations

import datetime
import email
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.email_utils import connect_imap, decode_mime_header, load_imap_credentials
from common.io_utils import (
    CONFIGS_DIR,
    OUTPUT_DIR,
    STATE_DIR,
    dump_json,
    ensure_data_dirs,
    load_email_ingest_config,
    load_json,
    load_yaml,
)
from common.runtime import is_test_mode


PODCAST_NAME = "Junkyard Racoon Radio"
PODCAST_DIR = OUTPUT_DIR / "podcast"
STATE_PATH = STATE_DIR / "podcast_state.json"
PREVIOUS_CONTEXT_PATH = STATE_DIR / "podcast_previous_episodes.txt"
PODCAST_SCRIPT = Path(__file__).resolve().parent.parent / "podcast_gemini_tts_ver2.py"

SENDER_FILTER = "tim.alamenciak@gmail.com"
SUBJECT_FILTER = "podcast"
LOOKBACK_DAYS = 14


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> dict:
    state = load_json(STATE_PATH, default={}) or {}
    state.setdefault("episode_count", 0)
    state.setdefault("episodes", [])
    return state


def save_state(state: dict) -> None:
    state["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    dump_json(STATE_PATH, state)


# ---------------------------------------------------------------------------
# Previous episode context
# ---------------------------------------------------------------------------

def build_previous_context(episodes: list[dict]) -> str:
    """Format last 10 episode summaries as plain text for the LLM prompt."""
    if not episodes:
        return ""
    lines: list[str] = []
    for ep in episodes[-10:]:
        num = ep.get("episode_number", "?")
        title = ep.get("title", f"Episode {num}")
        date = ep.get("date", "unknown")
        summary = (ep.get("notes_summary") or "").strip()
        lines.append(f"Episode {num} ({date}): {title}")
        if summary:
            lines.append(f"Summary: {summary}")
        lines.append("")
    return "\n".join(lines).strip()


def write_previous_context_file(episodes: list[dict]) -> None:
    context = build_previous_context(episodes)
    PREVIOUS_CONTEXT_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREVIOUS_CONTEXT_PATH.write_text(context, encoding="utf-8")


# ---------------------------------------------------------------------------
# PDF attachment extraction
# ---------------------------------------------------------------------------

def extract_pdf_attachments(message: email.message.Message) -> list[tuple[str, bytes]]:
    """Return list of (filename, data) for PDF attachments in an email."""
    from email.header import decode_header as _decode_header

    pdfs: list[tuple[str, bytes]] = []
    if not message.is_multipart():
        return pdfs

    for part in message.walk():
        content_type = (part.get_content_type() or "").lower()
        content_disposition = (part.get("Content-Disposition") or "").lower()

        is_pdf = content_type == "application/pdf" or (
            "attachment" in content_disposition and
            (part.get_filename() or "").lower().endswith(".pdf")
        )
        if not is_pdf:
            continue

        raw_filename = part.get_filename() or "attachment.pdf"
        decoded_parts = _decode_header(raw_filename)
        filename = ""
        for frag, enc in decoded_parts:
            if isinstance(frag, bytes):
                filename += frag.decode(enc or "utf-8", errors="replace")
            else:
                filename += frag

        payload = part.get_payload(decode=True)
        if payload:
            pdfs.append((filename, payload))

    return pdfs


# ---------------------------------------------------------------------------
# Episode generation
# ---------------------------------------------------------------------------

def python_command() -> list[str]:
    launcher = shutil.which("py")
    if os.name == "nt" and launcher:
        return [launcher, "-3"]
    return [sys.executable]


def extract_notes_summary(notes_file: Path) -> str:
    """Pull the episode summary paragraph from the generated notes Markdown."""
    if not notes_file.exists():
        return ""
    content = notes_file.read_text(encoding="utf-8")
    match = re.search(r"## Episode [Ss]ummary\n+(.*?)(?=\n##|\Z)", content, re.DOTALL)
    if match:
        summary = match.group(1).strip()
        return summary[:500].rsplit(" ", 1)[0] + "..." if len(summary) > 500 else summary
    # Fallback: first non-heading lines
    lines = [ln.strip() for ln in content.split("\n") if ln.strip() and not ln.startswith("#")]
    return " ".join(lines)[:300]


def load_api_keys() -> tuple[str, str]:
    """Read Anthropic and Gemini API keys from llm.yaml, falling back to env vars."""
    try:
        llm_cfg = load_yaml(CONFIGS_DIR / "llm.yaml")
    except Exception:
        llm_cfg = {}
    anthropic_key = (
        llm_cfg.get("anthropic_api_key", "").strip()
        or os.environ.get("ANTHROPIC_API_KEY", "").strip()
    )
    gemini_key = (
        llm_cfg.get("gemini_api_key", "").strip()
        or os.environ.get("GEMINI_API_KEY", "").strip()
    )
    return anthropic_key, gemini_key


def generate_episode(
    pdf_path: Path,
    episode_number: int,
    existing_episodes: list[dict],
) -> dict | None:
    """
    Run podcast_gemini_tts_ver2.py for one PDF and return the episode metadata dict,
    or None if generation failed.
    """
    anthropic_key, gemini_key = load_api_keys()
    if not anthropic_key or anthropic_key == "replace-me":
        print(
            "[podcast_email_ingest] ERROR: anthropic_api_key not set in llm.yaml",
            file=sys.stderr,
        )
        return None
    if not gemini_key or gemini_key == "replace-me":
        print(
            "[podcast_email_ingest] ERROR: gemini_api_key not set in llm.yaml",
            file=sys.stderr,
        )
        return None

    PODCAST_DIR.mkdir(parents=True, exist_ok=True)
    write_previous_context_file(existing_episodes)

    ep_slug = f"episode_{episode_number:03d}"
    title = f"{PODCAST_NAME} \u2014 Episode {episode_number}"
    output_mp3 = PODCAST_DIR / f"{ep_slug}.mp3"
    output_base = str(output_mp3.with_suffix(""))

    cmd = python_command() + [
        str(PODCAST_SCRIPT),
        str(pdf_path),
        "-o", str(output_mp3),
        "--title", title,
    ]
    has_context = (
        PREVIOUS_CONTEXT_PATH.exists() and
        PREVIOUS_CONTEXT_PATH.stat().st_size > 0
    )
    if has_context:
        cmd += ["--previous-context", str(PREVIOUS_CONTEXT_PATH)]

    # Inject API keys into the subprocess environment
    env = os.environ.copy()
    env["ANTHROPIC_API_KEY"] = anthropic_key
    env["GEMINI_API_KEY"] = gemini_key

    print(f"[podcast_email_ingest] Generating {title} from {pdf_path.name}")
    result = subprocess.run(cmd, env=env, check=False)
    if result.returncode != 0:
        print(
            f"[podcast_email_ingest] ERROR: episode generation failed (exit {result.returncode})",
            file=sys.stderr,
        )
        return None

    transcript_file = Path(f"{output_base}_transcript.md")
    notes_file = Path(f"{output_base}_notes.md")

    return {
        "episode_number": episode_number,
        "title": title,
        "date": datetime.date.today().isoformat(),
        "mp3_filename": output_mp3.name,
        "mp3_size_bytes": output_mp3.stat().st_size if output_mp3.exists() else 0,
        "transcript_filename": transcript_file.name if transcript_file.exists() else "",
        "notes_filename": notes_file.name if notes_file.exists() else "",
        "notes_summary": extract_notes_summary(notes_file),
    }


# ---------------------------------------------------------------------------
# RSS feed generation
# ---------------------------------------------------------------------------

def _xml(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def generate_rss_feed(episodes: list[dict], public_url: str) -> str:
    """Return iTunes-compatible RSS 2.0 XML for the podcast."""
    podcast_url = public_url.rstrip("/") + "/podcast/"
    feed_url = podcast_url + "feed.xml"
    now_rfc822 = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%a, %d %b %Y %H:%M:%S +0000"
    )

    items: list[str] = []
    for ep in reversed(episodes):
        num = ep.get("episode_number", 0)
        title = ep.get("title", f"Episode {num}")
        date_str = ep.get("date", "")
        try:
            pub_date = datetime.datetime.fromisoformat(date_str).strftime(
                "%a, %d %b %Y %H:%M:%S +0000"
            )
        except Exception:
            pub_date = now_rfc822

        mp3_filename = ep.get("mp3_filename", "")
        mp3_size = ep.get("mp3_size_bytes", 0)
        mp3_url = podcast_url + mp3_filename if mp3_filename else ""
        summary = ep.get("notes_summary", "")
        notes_filename = ep.get("notes_filename", "")
        notes_link = (
            f"\n      <link>{_xml(podcast_url + notes_filename)}</link>"
            if notes_filename else ""
        )
        enclosure = (
            f'<enclosure url="{_xml(mp3_url)}" length="{mp3_size}" type="audio/mpeg"/>'
            if mp3_url else ""
        )

        items.append(
            f"    <item>\n"
            f"      <title>{_xml(title)}</title>\n"
            f"      <itunes:title>{_xml(title)}</itunes:title>\n"
            f"      <pubDate>{pub_date}</pubDate>\n"
            f"      <itunes:episode>{num}</itunes:episode>\n"
            f"      <itunes:episodeType>full</itunes:episodeType>\n"
            f"      <description>{_xml(summary)}</description>\n"
            f"      <itunes:summary>{_xml(summary)}</itunes:summary>\n"
            f"      {enclosure}{notes_link}\n"
            f"      <guid isPermaLink=\"false\">{_xml(podcast_url)}episode_{num:03d}</guid>\n"
            f"    </item>"
        )

    items_xml = "\n".join(items)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"\n'
        '  xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"\n'
        '  xmlns:atom="http://www.w3.org/2005/Atom"\n'
        '  xmlns:content="http://purl.org/rss/1.0/modules/content/">\n'
        "  <channel>\n"
        f"    <title>{_xml(PODCAST_NAME)}</title>\n"
        f"    <link>{_xml(podcast_url)}</link>\n"
        "    <description>Academic journal articles discussed by Dr. Elena and Marcus. "
        "Produced by the Racoon Lab at Wilfrid Laurier University. "
        "Scripts by Claude (Anthropic), audio by Gemini TTS (Google). CC BY 4.0.</description>\n"
        "    <language>en-ca</language>\n"
        "    <copyright>Racoon Lab, Wilfrid Laurier University. CC BY 4.0.</copyright>\n"
        "    <itunes:author>Racoon Lab, Wilfrid Laurier University</itunes:author>\n"
        "    <itunes:summary>Dr. Elena and Marcus unpack academic research articles in ecology, "
        "conservation, and related fields. Produced by the Racoon Lab.</itunes:summary>\n"
        '    <itunes:category text="Science"/>\n'
        "    <itunes:explicit>false</itunes:explicit>\n"
        "    <itunes:type>episodic</itunes:type>\n"
        f'    <atom:link href="{_xml(feed_url)}" rel="self" type="application/rss+xml"/>\n'
        f"    <lastBuildDate>{now_rfc822}</lastBuildDate>\n"
        f"{items_xml}\n"
        "  </channel>\n"
        "</rss>"
    )


# ---------------------------------------------------------------------------
# IMAP helpers
# ---------------------------------------------------------------------------

def search_podcast_emails(client, lookback_days: int) -> list[str]:
    """Return UIDs of unseen messages that plausibly contain a podcast request."""
    since_date = (
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=lookback_days)
    ).strftime("%d-%b-%Y")
    status, data = client.uid(
        "search", None,
        f'UNSEEN SINCE {since_date} FROM "{SENDER_FILTER}" SUBJECT "{SUBJECT_FILTER}"',
    )
    if status != "OK" or not data or not data[0]:
        return []
    return [v for v in data[0].decode("utf-8", errors="replace").split() if v]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ensure_data_dirs()
    PODCAST_DIR.mkdir(parents=True, exist_ok=True)

    if is_test_mode():
        print("[podcast_email_ingest] test mode — skipping IMAP check and episode generation")
        return

    config_path = CONFIGS_DIR / "email_ingest.yaml"
    if not config_path.exists():
        print(f"[podcast_email_ingest] No email config at {config_path}; skipping")
        return

    config = load_email_ingest_config(config_path)
    if not config.get("enabled", True):
        print("[podcast_email_ingest] Email ingestion disabled; skipping")
        return

    try:
        username, password = load_imap_credentials(config)
    except RuntimeError as exc:
        print(f"[podcast_email_ingest] WARNING: {exc}", file=sys.stderr)
        return

    host = str(config.get("host", "imap.gmail.com"))
    port = int(config.get("port", 993))

    state = load_state()
    processed_keys: set[str] = {
        ep.get("source_email_key", "") for ep in state["episodes"]
    }
    new_episodes = 0
    client = None

    try:
        client = connect_imap(host=host, port=port, username=username, password=password)

        status, _ = client.select("INBOX", readonly=False)
        if status != "OK":
            print("[podcast_email_ingest] WARNING: could not select INBOX", file=sys.stderr)
            return

        uids = search_podcast_emails(client, LOOKBACK_DAYS)
        print(f"[podcast_email_ingest] {len(uids)} candidate email(s) from {SENDER_FILTER}")

        for uid in uids:
            fetch_status, msg_data = client.uid("fetch", uid, "(RFC822)")
            if fetch_status != "OK" or not msg_data or not msg_data[0]:
                continue

            message = email.message_from_bytes(msg_data[0][1])
            subject = decode_mime_header(message.get("Subject", ""))
            message_id = (message.get("Message-ID") or "").strip()
            email_key = f"INBOX::{message_id or uid}"

            if email_key in processed_keys:
                print(f"[podcast_email_ingest] Already processed: {subject!r}")
                continue

            pdfs = extract_pdf_attachments(message)
            if not pdfs:
                print(f"[podcast_email_ingest] No PDF in: {subject!r}; skipping")
                continue

            for pdf_filename, pdf_data in pdfs:
                episode_number = state["episode_count"] + 1

                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(pdf_data)
                    tmp_path = Path(tmp.name)

                try:
                    ep_meta = generate_episode(
                        pdf_path=tmp_path,
                        episode_number=episode_number,
                        existing_episodes=state["episodes"],
                    )
                    if ep_meta:
                        ep_meta["source_email_key"] = email_key
                        ep_meta["source_pdf_filename"] = pdf_filename
                        state["episodes"].append(ep_meta)
                        state["episode_count"] = episode_number
                        processed_keys.add(email_key)
                        save_state(state)
                        new_episodes += 1
                        print(
                            f"[podcast_email_ingest] Episode {episode_number} saved to {PODCAST_DIR}"
                        )
                finally:
                    tmp_path.unlink(missing_ok=True)

    except Exception as exc:
        print(f"[podcast_email_ingest] ERROR: {exc}", file=sys.stderr)
        raise
    finally:
        if client is not None:
            try:
                client.logout()
            except Exception:
                pass

    # Always regenerate the RSS feed (catches new episodes or first-run)
    try:
        output_cfg = load_yaml(CONFIGS_DIR / "output.yaml")
    except Exception:
        output_cfg = {}
    static_cfg = output_cfg.get("static_site", {})
    public_url = str(static_cfg.get("public_url", "https://lab.tim-a.ca/digest/")).rstrip("/") + "/"

    feed_xml = generate_rss_feed(state["episodes"], public_url)
    feed_path = PODCAST_DIR / "feed.xml"
    feed_path.write_text(feed_xml, encoding="utf-8")
    print(f"[podcast_email_ingest] RSS feed written → {feed_path}")
    print(f"[podcast_email_ingest] Done. {new_episodes} new episode(s) generated.")


if __name__ == "__main__":
    main()
