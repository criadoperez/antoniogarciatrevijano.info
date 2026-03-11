#!/usr/bin/env python3
"""
download_audios.py — Download iVoox audio files listed in ivoox_links.txt

Output per file:
  ficheros/publicos/audios/{id}.{ext}       — audio in original format
  ficheros/publicos/audios/{id}.info.json   — full yt-dlp metadata

Resumable: .yt-dlp-archive.txt tracks completed downloads; safe to Ctrl+C and re-run.

Usage:
    pip install yt-dlp
    python download_audios.py
"""

import sys
from pathlib import Path

try:
    import yt_dlp
except ImportError:
    print("yt-dlp not installed. Run: pip install yt-dlp")
    sys.exit(1)

BASE_DIR = Path(__file__).parent
LINKS_FILE = BASE_DIR / "ivoox_links.txt"
OUTPUT_DIR = BASE_DIR / "ficheros" / "publicos" / "audios"
ARCHIVE_FILE = OUTPUT_DIR / ".yt-dlp-archive.txt"
ERRORS_FILE = OUTPUT_DIR / ".download-errors.txt"


def main():
    if not LINKS_FILE.exists():
        print(f"Links file not found: {LINKS_FILE}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    with open(LINKS_FILE) as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    print(f"Links file: {LINKS_FILE}")
    print(f"Output dir: {OUTPUT_DIR}")
    print(f"Total URLs: {len(urls)}")
    if ARCHIVE_FILE.exists():
        with open(ARCHIVE_FILE) as arc:
            already_done = sum(1 for _ in arc)
        print(f"Already downloaded (archive): {already_done}")
    print()

    ydl_opts = {
        # Keep original format — iVoox serves MP3 128kbps; bestaudio gets it as-is
        "format": "bestaudio/best",
        # Output filename: use iVoox numeric ID as stem (e.g. 1463648.mp3)
        "outtmpl": str(OUTPUT_DIR / "%(id)s.%(ext)s"),
        # Save full metadata JSON alongside each audio file
        "writeinfojson": True,
        # Resume support: tracks downloaded IDs, skips on re-run
        "download_archive": str(ARCHIVE_FILE),
        # Conservative delays to avoid iVoox rate-limiting (~3–8s per file, ~3–4h total)
        "sleep_interval": 3,
        "max_sleep_interval": 8,
        # Don't abort on unavailable/deleted files — log and continue
        "ignoreerrors": True,
        # Retry on transient network errors
        "retries": 5,
        "fragment_retries": 5,
    }

    error_count = 0

    class ErrorLogger:
        def debug(self, msg): pass
        def warning(self, msg): pass
        def error(self, msg):
            nonlocal error_count
            error_count += 1
            with open(ERRORS_FILE, "a") as f:
                f.write(msg + "\n")
            print(f"  [ERROR] {msg}")

    ydl_opts["logger"] = ErrorLogger()

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download(urls)

    print(f"\nFinished. Errors: {error_count}")
    if error_count:
        print(f"Error log: {ERRORS_FILE}")


if __name__ == "__main__":
    main()
