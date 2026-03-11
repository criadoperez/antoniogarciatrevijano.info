#!/usr/bin/env python3
"""
transcribe_audios.py — Transcribe iVoox audio files using faster-whisper large-v3 (GPU).

Input:  ficheros/publicos/audios/{id}.mp3  +  {id}.info.json
Output per file:
  {id}.md   — clean transcript with YAML frontmatter (feeds RAG pipeline + static site)
  {id}.srt  — timestamped subtitles (for site player sync, future use)

Resumable: skips files where {id}.md already exists. Safe to Ctrl+C and re-run.

Requirements:
    pip install faster-whisper tqdm
    CUDA toolkit + NVIDIA drivers (RTX 3070 Ti — large-v3 fits in FP16, ~3.5 GB VRAM)

Usage:
    python transcribe_audios.py
"""

import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from faster_whisper import WhisperModel
except ImportError:
    print("faster-whisper not installed. Run: pip install faster-whisper")
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm not installed. Run: pip install tqdm")
    sys.exit(1)

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
AUDIO_DIR = BASE_DIR / "ficheros" / "publicos" / "audios"

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac", ".wma"}

MODEL_SIZE    = "large-v3"   # Best available Whisper model
LANGUAGE      = "es"         # Force Spanish — skip language detection
BEAM_SIZE     = 5            # Standard beam search width, good quality/speed balance
COMPUTE_TYPE  = "float16"    # FP16 on GPU: ~3.5 GB VRAM, fastest
DEVICE        = "cuda"
# VAD (voice activity detection) removes silence/music, improves accuracy
VAD_FILTER    = True
VAD_MIN_SILENCE_MS = 500

FAILURES_FILE = AUDIO_DIR / ".transcribe-failures.txt"

# ── Helpers ───────────────────────────────────────────────────────────────────

def srt_time(seconds: float) -> str:
    """Convert float seconds to SRT timestamp: HH:MM:SS,mmm"""
    # Work in integer milliseconds to avoid floating-point rounding producing ms=1000
    total_ms = round(seconds * 1000)
    ms = total_ms % 1000
    total_s = total_ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{srt_time(seg.start)} --> {srt_time(seg.end)}\n")
            f.write(f"{seg.text.strip()}\n\n")


def parse_date(raw: str) -> str:
    """Convert yt-dlp YYYYMMDD → ISO YYYY-MM-DD. Returns '' if malformed."""
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw  # already formatted or empty


def load_info(audio_path: Path) -> dict:
    """Load {stem}.info.json; return empty dict if missing."""
    p = audio_path.with_suffix(".info.json")
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_md(segments: list, info: dict, audio_path: Path) -> None:
    """Write YAML-frontmatter markdown transcript."""
    title       = info.get("title") or audio_path.stem
    date        = parse_date(info.get("upload_date", ""))
    uploader    = info.get("uploader") or "iVoox"
    ivoox_url   = info.get("webpage_url") or info.get("original_url") or ""
    ivoox_id    = info.get("id") or audio_path.stem
    duration    = int(info.get("duration") or 0)
    description = (info.get("description") or "").strip()

    transcript = "\n\n".join(seg.text.strip() for seg in segments if seg.text.strip())

    # YAML frontmatter — title and uploader are JSON-quoted to handle special chars
    frontmatter = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f'date: "{date}"',
        f"uploader: {json.dumps(uploader, ensure_ascii=False)}",
        f'ivoox_url: "{ivoox_url}"',
        f'ivoox_id: "{ivoox_id}"',
        f"duration_seconds: {duration}",
        f'audio_filename: "{audio_path.name}"',
        'audio_cid: ""',      # filled in later by sync_to_ipfs.py
        "---",
        "",
    ]

    body = []
    if description:
        # Include original episode description as a blockquote before the transcript
        body += [f"> {line}" if line else ">" for line in description.splitlines()]
        body += ["", "---", ""]

    body.append(transcript)

    # Append comments if present in info.json (fetched by download_audios.py --writecomments)
    comments = info.get("comments") or []
    if comments:
        body += ["", "---", "", "## Comentarios", ""]
        for c in comments:
            author    = (c.get("author") or "Anónimo").strip()
            text      = (c.get("text") or "").strip()
            timestamp = c.get("timestamp")
            date_str  = ""
            if timestamp:
                date_str = f" · {datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime('%Y-%m-%d')}"
            if text:
                body += [f"**{author}**{date_str}", "", text, ""]

    md_path = audio_path.with_suffix(".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(frontmatter + body) + "\n")


def human_duration(seconds: float) -> str:
    td = timedelta(seconds=int(seconds))
    h, rem = divmod(td.seconds + td.days * 86400, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    return f"{m}m {s:02d}s"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if not AUDIO_DIR.exists():
        print(f"Audio directory not found: {AUDIO_DIR}")
        sys.exit(1)

    all_audio = sorted(
        p for p in AUDIO_DIR.iterdir()
        if p.suffix.lower() in AUDIO_EXTENSIONS
    )

    if not all_audio:
        print(f"No audio files found in {AUDIO_DIR}")
        sys.exit(0)

    todo = [p for p in all_audio if not p.with_suffix(".md").exists()]
    done_count = len(all_audio) - len(todo)

    print(f"Audio files : {len(all_audio)} total")
    print(f"Already done: {done_count}")
    print(f"To transcribe: {len(todo)}")
    if not todo:
        print("All done!")
        sys.exit(0)

    print(f"\nLoading faster-whisper {MODEL_SIZE} on {DEVICE} ({COMPUTE_TYPE})...")
    t0 = time.time()
    model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)
    print(f"Model loaded in {time.time() - t0:.1f}s\n")

    failures = []
    total_audio_seconds = 0.0
    total_wall_seconds  = 0.0

    progress = tqdm(todo, unit="file", dynamic_ncols=True)
    for audio_path in progress:
        progress.set_description(audio_path.name[:50])

        info = load_info(audio_path)
        audio_duration = float(info.get("duration") or 0)

        t_start = time.time()
        try:
            segments_gen, _ = model.transcribe(
                str(audio_path),
                language=LANGUAGE,
                beam_size=BEAM_SIZE,
                vad_filter=VAD_FILTER,
                vad_parameters={"min_silence_duration_ms": VAD_MIN_SILENCE_MS},
            )
            segments = list(segments_gen)   # materialise once for both outputs
        except Exception as e:
            tqdm.write(f"  [FAIL] {audio_path.name}: {e}")
            failures.append(f"{audio_path.name}: {e}")
            with open(FAILURES_FILE, "a") as f:
                f.write(f"{audio_path.name}: {e}\n")
            continue

        elapsed = time.time() - t_start
        total_audio_seconds += audio_duration
        total_wall_seconds  += elapsed

        # Real-time factor: 1.0 means as fast as real-time
        rtf = elapsed / audio_duration if audio_duration > 0 else 0.0

        write_srt(segments, audio_path.with_suffix(".srt"))
        write_md(segments, info, audio_path)

        tqdm.write(
            f"  ✓ {audio_path.stem}  "
            f"audio={human_duration(audio_duration)}  "
            f"wall={human_duration(elapsed)}  "
            f"RTF={rtf:.2f}x  "
            f"segments={len(segments)}"
        )

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"Transcribed : {len(todo) - len(failures)}/{len(todo)} files")
    print(f"Total audio : {human_duration(total_audio_seconds)}")
    print(f"Total wall  : {human_duration(total_wall_seconds)}")
    if total_wall_seconds > 0 and total_audio_seconds > 0:
        avg_rtf = total_wall_seconds / total_audio_seconds
        print(f"Avg RTF     : {avg_rtf:.2f}x  ({1/avg_rtf:.1f}× faster than real-time)")

    if failures:
        print(f"\nFailures ({len(failures)}) logged to: {FAILURES_FILE}")
        for f in failures:
            print(f"  - {f}")


if __name__ == "__main__":
    main()
