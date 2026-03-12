#!/usr/bin/env python3
"""
transcribe_audios.py — Transcribe + diarize iVoox audio using WhisperX.

Pipeline per file:
  1. Transcribe  — faster-whisper large-v3, float16, beam_size=10, forced Spanish
  2. Align       — forced word-level alignment (wav2vec2)
  3. Diarize     — pyannote speaker-diarization-3.1 (requires HF_TOKEN)
  4. Assign      — merge speaker turns into transcript segments

Input:   ficheros/publicos/audios/{id}.mp3  +  {id}.info.json
Output:  {id}.md   — YAML frontmatter + speaker-labelled transcript
         {id}.srt  — timestamped subtitles with speaker labels

Resumable: skips {id}.md if it already exists. Safe to Ctrl+C and re-run.

Requirements:
    pip install whisperx
    CUDA + NVIDIA drivers (RTX 3070 Ti — large-v3 float16 fits in ~4 GB VRAM)
    HF_TOKEN env var — HuggingFace token with access to:
      https://huggingface.co/pyannote/speaker-diarization-3.1
      https://huggingface.co/pyannote/segmentation-3.0

Usage:
    python transcribe_audios.py  # HF_TOKEN is read from .env
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Load .env from project root
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                os.environ.setdefault(_k.strip(), _v.strip())

try:
    import whisperx
    from whisperx.diarize import DiarizationPipeline, assign_word_speakers
except ImportError:
    print("whisperx not installed. Run: pip install whisperx")
    sys.exit(1)

try:
    from tqdm import tqdm
except ImportError:
    print("tqdm not installed. Run: pip install tqdm")
    sys.exit(1)

# ── Configuration ─────────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent
AUDIO_DIR = BASE_DIR / "ficheros" / "publicos" / "audios"

AUDIO_EXTENSIONS = {".mp3", ".m4a", ".ogg", ".opus", ".wav", ".flac", ".aac", ".wma"}

MODEL_SIZE   = "large-v3"  # Best available Whisper model
LANGUAGE     = "es"        # Force Spanish — skip language detection
BEAM_SIZE    = 10          # Max practical quality (default is 5)
COMPUTE_TYPE = "float16"   # FP16: ~4 GB VRAM, native Whisper precision
DEVICE       = "cuda"
BATCH_SIZE   = 4           # Lower batch size to fit ASR + alignment + diarization in 8 GB VRAM

DIARIZATION_MODEL = "pyannote/speaker-diarization-3.1"

MIN_SPEAKERS = 1
MAX_SPEAKERS = 10          # Upper bound; pyannote detects the actual number

# HuggingFace token for pyannote diarization models.
# Get a token at: https://huggingface.co/settings/tokens
# Accept terms at: https://huggingface.co/pyannote/speaker-diarization-3.1
#                  https://huggingface.co/pyannote/segmentation-3.0
HF_TOKEN = os.environ.get("HF_TOKEN", "")

FAILURES_FILE = AUDIO_DIR / ".transcribe-failures.txt"

# ── Helpers ───────────────────────────────────────────────────────────────────

def srt_time(seconds: float) -> str:
    """Convert float seconds to SRT timestamp: HH:MM:SS,mmm"""
    total_ms = round(seconds * 1000)
    ms = total_ms % 1000
    total_s = total_ms // 1000
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def translate_speaker(label: str) -> str:
    """Translate pyannote speaker labels to Spanish: SPEAKER_XX -> LOCUTOR_XX."""
    return label.replace("SPEAKER_", "LOCUTOR_").replace("UNKNOWN", "DESCONOCIDO")


def write_srt(segments: list, path: Path) -> None:
    idx = 1
    with open(path, "w", encoding="utf-8") as f:
        for seg in segments:
            text = seg.get("text", "").strip()
            if not text:
                continue
            speaker = translate_speaker(seg.get("speaker", "UNKNOWN"))
            f.write(f"{idx}\n")
            f.write(f"{srt_time(seg['start'])} --> {srt_time(seg['end'])}\n")
            f.write(f"[{speaker}] {text}\n\n")
            idx += 1


def parse_date(raw: str) -> str:
    """Convert yt-dlp YYYYMMDD → ISO YYYY-MM-DD. Returns '' if malformed."""
    raw = (raw or "").strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def load_info(audio_path: Path) -> dict:
    """Load {stem}.info.json; return empty dict if missing."""
    p = audio_path.with_suffix(".info.json")
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_md(segments: list, info: dict, audio_path: Path) -> None:
    """Write YAML-frontmatter markdown transcript grouped by speaker turns."""
    title       = info.get("title") or audio_path.stem
    date        = parse_date(info.get("upload_date", ""))
    uploader    = info.get("uploader") or "iVoox"
    ivoox_url   = info.get("webpage_url") or info.get("original_url") or ""
    ivoox_id    = info.get("id") or audio_path.stem
    duration    = int(info.get("duration") or 0)
    description = (info.get("description") or "").strip()

    # Collect unique speaker IDs for frontmatter
    speakers = sorted({translate_speaker(seg.get("speaker", "UNKNOWN")) for seg in segments if seg.get("text", "").strip()})

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
        f"speakers: {json.dumps(speakers)}",
        "---",
        "",
    ]

    body = []
    if description:
        body += [f"> {line}" if line else ">" for line in description.splitlines()]
        body += ["", "---", ""]

    # Group consecutive segments by speaker into turns
    turns = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        speaker = translate_speaker(seg.get("speaker", "UNKNOWN"))
        if turns and turns[-1][0] == speaker:
            turns[-1][1].append(text)
        else:
            turns.append((speaker, [text]))

    for speaker, texts in turns:
        body.append(f"**{speaker}:** {' '.join(texts)}")
        body.append("")

    # Append comments from info.json
    comments = info.get("comments") or []
    if comments:
        body += ["---", "", "## Comentarios", ""]
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
    if not HF_TOKEN:
        print("Error: HF_TOKEN environment variable not set.")
        print("  Get a token : https://huggingface.co/settings/tokens")
        print("  Accept terms: https://huggingface.co/pyannote/speaker-diarization-3.1")
        print("  Accept terms: https://huggingface.co/pyannote/segmentation-3.0")
        print("\nSet HF_TOKEN in .env and re-run.")
        sys.exit(1)

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

    # ── Load all models once ───────────────────────────────────────────────────
    print(f"\nLoading WhisperX {MODEL_SIZE} on {DEVICE} ({COMPUTE_TYPE}, beam_size={BEAM_SIZE})...")
    t0 = time.time()
    asr_model = whisperx.load_model(
        MODEL_SIZE,
        DEVICE,
        compute_type=COMPUTE_TYPE,
        language=LANGUAGE,
        asr_options={"beam_size": BEAM_SIZE},
    )
    print(f"ASR model loaded in {time.time() - t0:.1f}s")

    print("Loading alignment model (wav2vec2 es)...")
    t0 = time.time()
    align_model, align_metadata = whisperx.load_align_model(
        language_code=LANGUAGE,
        device=DEVICE,
    )
    print(f"Alignment model loaded in {time.time() - t0:.1f}s")

    print(f"Loading diarization model ({DIARIZATION_MODEL})...")
    t0 = time.time()
    diarize_model = DiarizationPipeline(
        model_name=DIARIZATION_MODEL,
        token=HF_TOKEN,
        device=DEVICE,
    )
    print(f"Diarization model loaded in {time.time() - t0:.1f}s\n")

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
            # 1. Load audio
            audio = whisperx.load_audio(str(audio_path))

            # 2. Transcribe
            result = asr_model.transcribe(audio, batch_size=BATCH_SIZE, language=LANGUAGE)

            # 3. Align (word-level timestamps — required for accurate speaker assignment)
            result = whisperx.align(
                result["segments"],
                align_model,
                align_metadata,
                audio,
                DEVICE,
                return_char_alignments=False,
            )

            # 4. Diarize
            diarize_segments = diarize_model(
                audio,
                min_speakers=MIN_SPEAKERS,
                max_speakers=MAX_SPEAKERS,
            )

            # 5. Assign speakers to transcript segments
            result = assign_word_speakers(diarize_segments, result)
            segments = result["segments"]

        except Exception as e:
            tqdm.write(f"  [FAIL] {audio_path.name}: {e}")
            failures.append(f"{audio_path.name}: {e}")
            with open(FAILURES_FILE, "a") as f:
                f.write(f"{audio_path.name}: {e}\n")
            continue

        elapsed = time.time() - t_start
        total_audio_seconds += audio_duration
        total_wall_seconds  += elapsed

        rtf = elapsed / audio_duration if audio_duration > 0 else 0.0
        speaker_ids = sorted({translate_speaker(seg.get("speaker", "UNKNOWN")) for seg in segments if seg.get("text", "").strip()})

        write_srt(segments, audio_path.with_suffix(".srt"))
        write_md(segments, info, audio_path)

        tqdm.write(
            f"  ✓ {audio_path.stem}  "
            f"audio={human_duration(audio_duration)}  "
            f"wall={human_duration(elapsed)}  "
            f"RTF={rtf:.2f}x  "
            f"speakers={len(speaker_ids)} ({', '.join(speaker_ids)})"
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
