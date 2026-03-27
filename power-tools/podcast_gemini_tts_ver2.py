#!/usr/bin/env python3
"""
podcast_gemini_tts.py — Convert a PDF article to a multi-speaker podcast audio file.

Complete pipeline:
  1. Extract & clean text from PDF
  2. Generate podcast script using Claude API (with optional previous episode context)
  3. Generate episode notes with full references using Claude
  4. Convert script to MP3 using Gemini TTS (chunked to < 4 min per call)
  5. Output: MP3 (with episode notes in metadata), Markdown transcript, Markdown episode notes

Usage:
    export ANTHROPIC_API_KEY="your-claude-key"
    export GEMINI_API_KEY="your-gemini-key"

    # Basic usage
    python podcast_gemini_tts.py article.pdf -o episode.mp3 --title "AI Unplugged"

    # With previous episode context (hosts can refer back to earlier episodes)
    python podcast_gemini_tts.py article.pdf \
        --title "Episode 13: Prairie Recovery" \
        --previous-context previous_episodes.txt

    # Custom speakers
    python podcast_gemini_tts.py article.pdf \
        --title "Episode 12" \
        --speakers "Dr. Elena=Kore" "Marcus=Puck"

    # Single speaker
    python podcast_gemini_tts.py article.pdf --single --voice Kore

    # Skip script generation (provide pre-written script)
    python podcast_gemini_tts.py article.pdf --script my_script.md

Outputs:
    episode.mp3              — The podcast audio
    episode_transcript.md    — Full script as Markdown
    episode_notes.md         — Episode notes with references

Requirements:
    pip install pypdf anthropic google-genai pydub mutagen
"""

import argparse
import os
import re
import sys
import time
import wave
from pathlib import Path

import anthropic
from google import genai
from google.genai import types

try:
    from pypdf import PdfReader
except ImportError:
    from PyPDF2 import PdfReader


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2  # 16-bit
CHANNELS = 1

# Default chunk size tuned so each TTS call produces < 4 min audio.
# ~3000 tokens of dialogue ≈ 2.5–3.5 min of speech.
DEFAULT_MAX_TOKENS = 3000
CHARS_PER_TOKEN = 4

MAX_RETRIES = 5
RETRY_BASE_DELAY = 5

MAX_CHUNK_AUDIO_SECS = 3.5 * 60  # 3m30s — safe margin under 4 min


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: str) -> str:
    """Extract all text from a PDF file."""
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text()
    return text


def clean_text(text: str) -> str:
    """Clean extracted PDF text."""
    text = text.replace('\f', '\n')
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'^\d+\s*$', '', text, flags=re.MULTILINE)
    return text.strip()


# ---------------------------------------------------------------------------
# Script generation via Claude
# ---------------------------------------------------------------------------

def _build_previous_context_block(context_path: str | None) -> str:
    """Load previous episode context file and format it for the prompt."""
    if not context_path:
        return ""
    path = Path(context_path)
    if not path.is_file():
        print(f"  ⚠ Previous context file not found: {path}. Proceeding without it.",
              file=sys.stderr)
        return ""
    context = path.read_text(encoding="utf-8").strip()
    if not context:
        return ""
    return f"""

PREVIOUS EPISODE CONTEXT:
The hosts have discussed related topics in previous episodes. They can naturally
refer back to these past discussions where relevant (e.g. "as we talked about
in our episode on...", "remember when we covered...", "building on what we
discussed last time about..."). Use these references sparingly and only when
they genuinely add to the current conversation.

Previous episode summaries:
{context}
"""


def generate_initial_script(
    client: anthropic.Anthropic,
    pdf_text: str,
    previous_context: str = "",
) -> str:
    """Generate initial 20-minute podcast script from article."""
    print("📝 Generating initial podcast script with Claude...")

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=20000,
        messages=[
            {
                "role": "user",
                "content": f"""You are an expert podcast producer. Write a comprehensive podcast script based on the scientific article below.

The script features two characters:
- Dr. Elena: Senior professor, warm but intellectually rigorous, occasionally wry, aware she's AI. She is a libertarian municipalist who follows the philosophies of Pyotr Kropotkin, Murray Bookchin and David Graeber.
- Marcus: Graduate student, genuinely curious, asks good follow-up questions, also AI-aware

Requirements:
1. PEDAGOGICAL VALIDITY: Follow Bloom's taxonomy - start with comprehension, move to application, analysis, synthesis. Use the Socratic method where Dr. Elena poses questions that guide Marcus to insights.

2. CHARACTER CONSISTENCY: 
   - Dr. Elena uses precise language, cites specific papers with authors and years
   - Marcus uses conversational language, expresses genuine confusion and "aha" moments
   - Both reference specific figures and quotes from the paper

3. COMPREHENSIVE COVERAGE: Include all major findings, methods, and implications. Bring in related work cited in the paper.

4. DEPTH: Explore why existing approaches fail, theoretical foundations, specific implementations, results and implications, limitations, and broader significance.

5. ENGAGEMENT: Make it conversational and natural. Include moments of discovery and intellectual engagement.

6. LENGTH: Approximately 15 minutes at standard podcast reading pace (~150 words/min = 3000-3500 words)

7. REFERENCES: When citing papers, always include full author names and years. At the end of the script, include a brief sign-off where Dr. Elena reminds listeners that full references are in the episode notes.
{previous_context}
Output ONLY the podcast script as plain text with speaker labels in this exact format:

Dr. Elena: [dialogue]
Marcus: [dialogue]

Do NOT use JSON, SSML, markdown, or any other formatting. Just the script.

Article:
{pdf_text}"""
            }
        ],
    )

    script = message.content[0].text
    print(f"✅ Initial script generated ({len(script):,} characters)")
    return script


def refine_script(client: anthropic.Anthropic, script: str) -> str:
    """Refine and enhance the initial script."""
    print("✨ Refining script with additional research and polish...")

    message = client.messages.create(
        model="claude-opus-4-6",
        max_tokens=20000,
        messages=[
            {
                "role": "user",
                "content": f"""You are an expert podcast script editor. Refine and enhance this podcast script.

Review for:

1. CHARACTER VOICE CONSISTENCY - Do Dr. Elena and Marcus sound consistent throughout? Fix any lapses.

2. PEDAGOGICAL FLOW - Does it build logically from comprehension to synthesis? Reorder if needed.

3. CITATIONS - Are citations accurate and well-integrated? Add missing ones if needed based on the original article.

4. PACING - Add [pause], [laughs], or [thinking] markers sparingly at natural moments.

5. CLARITY - Are technical concepts explained before use? Is jargon defined?

6. ENGAGEMENT - Are there moments of genuine discovery, disagreement resolved, or interesting questions raised and answered?

7. EXTEND TO 20 MINUTES - Add 5 more minutes of content by:
   - Deepening existing discussions
   - Adding more follow-up questions and insights
   - Bringing in additional related concepts from educational research
   - Exploring implications more thoroughly

8. SIGN-OFF - Ensure the episode ends with a natural sign-off that mentions episode notes are available with full references.

Output ONLY the refined script in the same format (plain text with character labels):

Dr. Elena: [dialogue]
Marcus: [dialogue]

No JSON, markdown, or other formatting.

Current script:
{script}"""
            }
        ],
    )

    refined = message.content[0].text
    print(f"✅ Script refined ({len(refined):,} characters)")
    return refined


def generate_episode_notes(
    client: anthropic.Anthropic,
    pdf_text: str,
    script: str,
    title: str | None = None,
) -> str:
    """Generate episode notes with full references from the article."""
    print("📋 Generating episode notes with Claude...")

    ep_title = title or "Untitled Episode"

    message = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4000,
        messages=[
            {
                "role": "user",
                "content": f"""Generate podcast episode notes for the following episode. The notes should be formatted as Markdown and include:

1. **Episode title and brief tagline** (1-2 sentences)

2. **Episode summary** (2-3 paragraph overview of what was discussed)

3. **Key topics covered** (bulleted list of major themes and takeaways)

4. **Full references** — This is the most important section. Extract ALL academic references mentioned in or relevant to the episode. Format each as a complete academic citation (authors, year, title, journal, volume, pages, DOI if available). Include:
   - The primary article being discussed
   - All papers cited within the primary article that are mentioned in the podcast
   - Any additional works referenced by the hosts

5. **Glossary** (brief definitions of key technical terms used in the episode)

6. **Credits** — "Produced by Raccoon Research Group. Script generated with Claude (Anthropic). Audio synthesized with Gemini TTS (Google). Licensed under CC BY 4.0."

Episode title: {ep_title}

Original article text (for extracting references):
{pdf_text[:15000]}

Podcast script (for context on what was discussed):
{script[:10000]}"""
            }
        ],
    )

    notes = message.content[0].text
    print(f"✅ Episode notes generated ({len(notes):,} characters)")
    return notes


# ---------------------------------------------------------------------------
# Text chunking & speaker parsing
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN


def chunk_by_speaker_turns(text: str, max_tokens: int) -> list[str]:
    """
    Split dialogue on speaker-turn boundaries so no turn is broken mid-sentence.
    Targets chunks that produce < 4 min audio.
    """
    max_chars = max_tokens * CHARS_PER_TOKEN

    # Match lines starting with speaker labels
    turn_pattern = re.compile(r'^([A-Za-z][A-Za-z .]+:)', re.MULTILINE)
    turns = turn_pattern.split(text)

    # Reassemble into complete turn blocks
    blocks: list[str] = []
    if turns[0].strip():
        blocks.append(turns[0].strip())
    for i in range(1, len(turns) - 1, 2):
        block = (turns[i] + turns[i + 1]).strip()
        if block:
            blocks.append(block)

    if not blocks:
        return _chunk_markdown(text, max_tokens)

    chunks: list[str] = []
    current_chunk = ""

    for block in blocks:
        if len(block) > max_chars:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            sub_chunks = _chunk_markdown(block, max_tokens)
            chunks.extend(sub_chunks)
        elif current_chunk and len(current_chunk) + len(block) + 2 > max_chars:
            chunks.append(current_chunk.strip())
            current_chunk = block
        else:
            current_chunk = f"{current_chunk}\n\n{block}" if current_chunk else block

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def _chunk_markdown(text: str, max_tokens: int) -> list[str]:
    """Fallback chunker for non-dialogue text."""
    max_chars = max_tokens * CHARS_PER_TOKEN
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]

    chunks: list[str] = []
    current_chunk = ""

    for para in paragraphs:
        if current_chunk and len(current_chunk) + len(para) + 2 > max_chars:
            chunks.append(current_chunk.strip())
            current_chunk = ""

        if len(para) > max_chars:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            sentences = re.split(r'(?<=[.!?])\s+', para)
            for sentence in sentences:
                if len(sentence) > max_chars:
                    words = sentence.split()
                    for word in words:
                        if len(current_chunk) + len(word) + 1 > max_chars:
                            if current_chunk:
                                chunks.append(current_chunk.strip())
                            current_chunk = word
                        else:
                            current_chunk = f"{current_chunk} {word}" if current_chunk else word
                elif len(current_chunk) + len(sentence) + 1 > max_chars:
                    if current_chunk:
                        chunks.append(current_chunk.strip())
                    current_chunk = sentence
                else:
                    current_chunk = f"{current_chunk} {sentence}" if current_chunk else sentence
        else:
            current_chunk = f"{current_chunk}\n\n{para}" if current_chunk else para

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def parse_speakers(speaker_args: list[str] | None) -> list[tuple[str, str]]:
    """Parse speaker definitions or use defaults."""
    if not speaker_args:
        return [("Dr. Elena", "Kore"), ("Marcus", "Puck")]

    speakers = []
    for s in speaker_args:
        if "=" not in s:
            print(f"ERROR: Speaker spec '{s}' must be in Name=Voice format.", file=sys.stderr)
            sys.exit(1)
        name, voice = s.split("=", 1)
        speakers.append((name.strip(), voice.strip()))

    if len(speakers) > 2:
        print("WARNING: Gemini TTS supports at most 2 speakers. Using the first two.", file=sys.stderr)
        speakers = speakers[:2]

    return speakers


def detect_speakers_in_text(text: str, all_speakers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return only speakers that appear in this text chunk."""
    present = [sp for sp in all_speakers if sp[0] in text]
    return present if present else all_speakers


# ---------------------------------------------------------------------------
# Gemini TTS
# ---------------------------------------------------------------------------

def build_multi_speaker_config(speakers: list[tuple[str, str]]) -> types.SpeechConfig:
    """Build multi-speaker voice config."""
    return types.SpeechConfig(
        multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
            speaker_voice_configs=[
                types.SpeakerVoiceConfig(
                    speaker=name,
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(
                            voice_name=voice,
                        )
                    ),
                )
                for name, voice in speakers
            ]
        )
    )


def build_single_speaker_config(voice: str) -> types.SpeechConfig:
    """Build single-speaker voice config."""
    return types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name=voice,
            )
        )
    )


def pcm_duration_secs(pcm_data: bytes) -> float:
    """Return duration of raw PCM data in seconds."""
    return len(pcm_data) / (SAMPLE_RATE * SAMPLE_WIDTH * CHANNELS)


def synthesize_chunk(
    client: genai.Client,
    text: str,
    speech_config: types.SpeechConfig,
    model: str,
) -> bytes:
    """Send chunk to Gemini TTS and return raw PCM bytes."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=speech_config,
                ),
            )

            part = response.candidates[0].content.parts[0]
            return part.inline_data.data

        except Exception as e:
            err_str = str(e).lower()
            is_retryable = any(
                kw in err_str
                for kw in ["429", "rate", "resource exhausted", "quota",
                           "500", "503", "unavailable", "internal"]
            )
            if is_retryable and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                print(
                    f"  ⚠ Retryable error on attempt {attempt}/{MAX_RETRIES}: {e!s:.100s}",
                    file=sys.stderr,
                )
                time.sleep(delay)
            else:
                raise


def concatenate_pcm(chunks: list[bytes]) -> bytes:
    """Concatenate PCM byte arrays."""
    return b"".join(chunks)


def pcm_to_mp3(
    pcm_data: bytes,
    output_path: str,
    bitrate: str = "192k",
    title: str | None = None,
    speakers: list[tuple[str, str]] | None = None,
    episode_notes: str | None = None,
):
    """Convert PCM to MP3 with metadata, optionally embedding episode notes."""
    from pydub import AudioSegment

    audio = AudioSegment(
        data=pcm_data,
        sample_width=SAMPLE_WIDTH,
        frame_rate=SAMPLE_RATE,
        channels=CHANNELS,
    )
    audio.export(output_path, format="mp3", bitrate=bitrate)

    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import (
            ID3, TIT2, TPE1, TPE2, TALB, TDRC, TCOP, COMM, TSSE,
            TXXX, ID3NoHeaderError,
        )
    except ImportError:
        print("  ⚠ mutagen not installed — skipping MP3 metadata.", file=sys.stderr)
        return

    try:
        mp3 = MP3(output_path, ID3=ID3)
    except ID3NoHeaderError:
        mp3 = MP3(output_path)
        mp3.add_tags()

    tags = mp3.tags

    from datetime import datetime
    year = str(datetime.now().year)

    artist = "Raccoon Research Group & Gemini TTS"
    copyright_text = f"© {year} Raccoon Research Group. Licensed under CC BY 4.0."
    license_url = "https://creativecommons.org/licenses/by/4.0/"

    if title:
        tags.add(TIT2(encoding=3, text=[title]))
        tags.add(TALB(encoding=3, text=[title]))
    tags.add(TPE1(encoding=3, text=[artist]))
    tags.add(TPE2(encoding=3, text=["Raccoon Research Group"]))
    tags.add(TDRC(encoding=3, text=[year]))
    tags.add(TCOP(encoding=3, text=[copyright_text]))
    tags.add(TSSE(encoding=3, text=["podcast_gemini_tts.py"]))

    comment_lines = [
        f"License: Creative Commons Attribution 4.0 International (CC BY 4.0)",
        f"License URL: {license_url}",
        f"Generated by: Raccoon Research Group using Claude + Gemini TTS",
    ]
    if speakers:
        voice_list = ", ".join(f"{name} ({voice})" for name, voice in speakers)
        comment_lines.append(f"Voices: {voice_list}")
    tags.add(COMM(encoding=3, lang="eng", desc="", text=["\n".join(comment_lines)]))

    # Embed episode notes as a custom TXXX frame (podcast apps can read this,
    # and it travels with the file)
    if episode_notes:
        tags.add(TXXX(encoding=3, desc="EPISODE_NOTES", text=[episode_notes]))

    mp3.save()
    print(f"  🏷️  MP3 metadata written (episode notes {'embedded' if episode_notes else 'not embedded'})")


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def write_transcript(script: str, output_base: str, title: str | None = None) -> str:
    """Write the full script as a Markdown file."""
    md_path = f"{output_base}_transcript.md"
    from datetime import datetime

    header = f"# {title or 'Podcast Transcript'}\n\n"
    header += f"*Generated: {datetime.now().strftime('%Y-%m-%d')}*\n"
    header += f"*Raccoon Research Group — CC BY 4.0*\n\n---\n\n"

    Path(md_path).write_text(header + script, encoding="utf-8")
    print(f"📝 Transcript saved → {md_path}")
    return md_path


def write_episode_notes(notes: str, output_base: str) -> str:
    """Write episode notes as a Markdown file."""
    md_path = f"{output_base}_notes.md"
    Path(md_path).write_text(notes, encoding="utf-8")
    print(f"📋 Episode notes saved → {md_path}")
    return md_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert a PDF article to a multi-speaker podcast MP3 "
                    "with transcript and episode notes.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input", help="Path to input PDF file")
    parser.add_argument("-o", "--output", default=None,
                        help="Output MP3 path (default: derived from --title)")
    parser.add_argument("-t", "--title", default=None,
                        help="Title for the podcast")
    parser.add_argument("--speakers", nargs="+", metavar="Name=Voice",
                        help='E.g. "Dr. Elena=Kore" "Marcus=Puck"')
    parser.add_argument("--single", action="store_true",
                        help="Single-speaker mode")
    parser.add_argument("--voice", default="Kore",
                        help="Voice for single-speaker mode")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help=f"Max tokens per TTS chunk (default: {DEFAULT_MAX_TOKENS}). "
                             "Lower = shorter audio per chunk, avoids degradation.")
    parser.add_argument("--bitrate", default="192k",
                        help="MP3 bitrate (default: 192k)")
    parser.add_argument("--script", default=None,
                        help="Path to a pre-written script file (skip Claude generation)")
    parser.add_argument("--previous-context", default=None,
                        help="Path to a text file with previous episode summaries. "
                             "Hosts will naturally reference these past discussions.")
    parser.add_argument("--anthropic-key", default=None,
                        help="Claude API key (or set ANTHROPIC_API_KEY)")
    parser.add_argument("--gemini-key", default=None,
                        help="Gemini API key (or set GEMINI_API_KEY)")
    parser.add_argument("--script-only", action="store_true",
                        help="Generate script and notes only, skip TTS audio synthesis")

    args = parser.parse_args()

    # Resolve output path
    if args.output is None:
        if args.title:
            slug = re.sub(r'[^\w\s-]', '', args.title.lower())
            slug = re.sub(r'[\s_-]+', '_', slug).strip('_')
            args.output = f"{slug}.mp3"
        else:
            args.output = "output.mp3"

    output_base = str(Path(args.output).with_suffix(""))

    # API keys
    anthropic_key = args.anthropic_key or os.environ.get("ANTHROPIC_API_KEY")
    gemini_key = args.gemini_key or os.environ.get("GEMINI_API_KEY")

    if not anthropic_key:
        print("ERROR: Set ANTHROPIC_API_KEY or pass --anthropic-key", file=sys.stderr)
        sys.exit(1)
    if not args.script_only and not gemini_key:
        print("ERROR: Set GEMINI_API_KEY or pass --gemini-key", file=sys.stderr)
        sys.exit(1)

    # Extract PDF
    input_path = Path(args.input)
    if not input_path.is_file():
        print(f"ERROR: File not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    print(f"📄 Extracting text from {input_path}")
    pdf_text = extract_text_from_pdf(str(input_path))
    cleaned_text = clean_text(pdf_text)
    print(f"   Extracted {len(pdf_text):,} → cleaned {len(cleaned_text):,} characters")

    # Generate or load script
    anthropic_client = anthropic.Anthropic(api_key=anthropic_key)

    if args.script:
        script_path = Path(args.script)
        if not script_path.is_file():
            print(f"ERROR: Script file not found: {script_path}", file=sys.stderr)
            sys.exit(1)
        script = script_path.read_text(encoding="utf-8")
        print(f"📝 Loaded pre-written script ({len(script):,} characters)")
    else:
        previous_context = _build_previous_context_block(args.previous_context)
        script = generate_initial_script(anthropic_client, cleaned_text, previous_context)
        script = refine_script(anthropic_client, script)

    # Write transcript
    write_transcript(script, output_base, title=args.title)

    # Generate episode notes
    episode_notes = generate_episode_notes(
        anthropic_client, cleaned_text, script, title=args.title,
    )
    write_episode_notes(episode_notes, output_base)

    if args.script_only:
        print("✅ Script-only mode — skipping TTS. Done!")
        return

    # Chunk script for TTS
    chunks = chunk_by_speaker_turns(script, args.max_tokens)
    total_est = estimate_tokens(script)
    print(f"🔪 Split into {len(chunks)} chunk(s) ({total_est:,} tokens, "
          f"targeting <3.5 min audio/chunk)")

    # TTS config
    if args.single:
        speech_config = build_single_speaker_config(args.voice)
        print(f"🎙️  Single-speaker: {args.voice}")
        speakers = None
    else:
        speakers = parse_speakers(args.speakers)
        print(f"🎙️  Multi-speaker: {', '.join(f'{n} ({v})' for n, v in speakers)}")

    # Synthesize
    gemini_client = genai.Client(api_key=gemini_key)
    pcm_parts: list[bytes] = []

    for i, chunk in enumerate(chunks, 1):
        chunk_tokens = estimate_tokens(chunk)
        print(f"  🔊 Chunk {i}/{len(chunks)} ({chunk_tokens:,} tokens) ...", end=" ", flush=True)

        if args.single:
            cfg = speech_config
        else:
            active = detect_speakers_in_text(chunk, speakers)
            cfg = build_multi_speaker_config(active)

        pcm = synthesize_chunk(gemini_client, chunk, cfg, "gemini-2.5-flash-preview-tts")
        pcm_parts.append(pcm)

        duration_s = pcm_duration_secs(pcm)
        status = "✅"
        if duration_s > MAX_CHUNK_AUDIO_SECS:
            status = "⚠️ LONG"
            print(f"\n  ⚠️  Chunk {i} produced {duration_s:.1f}s — consider lowering --max-tokens",
                  file=sys.stderr)
        print(f"{status} {duration_s:.1f}s")

    # Encode MP3
    print(f"🔗 Concatenating {len(pcm_parts)} segments")
    full_pcm = concatenate_pcm(pcm_parts)
    total_duration = pcm_duration_secs(full_pcm)

    print(f"💿 Encoding MP3 → {args.output}")
    pcm_to_mp3(
        full_pcm, args.output,
        bitrate=args.bitrate,
        title=args.title,
        speakers=speakers,
        episode_notes=episode_notes,
    )

    file_size = Path(args.output).stat().st_size
    print(f"\n✅ Done! {total_duration:.1f}s audio ({total_duration/60:.1f} min), "
          f"{file_size / 1_048_576:.1f} MB")
    print(f"   📁 {args.output}")
    print(f"   📁 {output_base}_transcript.md")
    print(f"   📁 {output_base}_notes.md")


if __name__ == "__main__":
    main()
