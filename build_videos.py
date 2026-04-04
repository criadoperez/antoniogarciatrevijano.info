"""
build_videos.py — Generate videos.json from metadata.tsv + overlap data.

Reads metadata.tsv from ficheros/privados/videos/ and produces
site/src/data/videos.json for the Astro build.
"""

import json
from pathlib import Path

META_FILE = Path("ficheros/privados/videos/metadata.tsv")
OVERLAP_FILE = Path("/tmp/video_audio_overlap_v2.json")
AUDIO_DIR = Path("ficheros/publicos/audios")
OUTPUT = Path("site/src/data/videos.json")

CHANNEL_THRESHOLD = 10


def load_audio_meta():
    meta = {}
    for info_path in sorted(AUDIO_DIR.glob("*.info.json")):
        aid = info_path.stem.replace(".info", "")
        try:
            with open(info_path) as f:
                info = json.load(f)
            meta[aid] = {"title": info.get("title", ""), "ivoox_id": aid}
        except Exception:
            pass
    return meta


def load_overlap():
    if not OVERLAP_FILE.exists():
        print(f"  WARNING: {OVERLAP_FILE} not found, no audio links")
        return {}
    with open(OVERLAP_FILE) as f:
        data = json.load(f)
    result = {}
    for m in data.get("high", []):
        result[m["vid_id"]] = m["audio_id"]
    return result


def format_date(raw):
    if len(raw) == 8:
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw


def format_duration(seconds):
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def main():
    if not META_FILE.exists():
        print(f"ERROR: {META_FILE} not found")
        return

    videos = []
    with open(META_FILE) as f:
        for line in f:
            parts = line.strip().split("\t")
            if len(parts) >= 5:
                vid_id = parts[0]
                videos.append({
                    "id": vid_id,
                    "duration": int(parts[1]),
                    "date": format_date(parts[2]),
                    "channel": parts[3],
                    "title": parts[4],
                    "duration_formatted": format_duration(int(parts[1])),
                    "youtube_url": f"https://www.youtube.com/watch?v={vid_id}",
                    "thumbnail": f"https://img.youtube.com/vi/{vid_id}/hqdefault.jpg",
                })

    print(f"Loaded {len(videos)} videos")

    # Channel grouping
    channel_counts = {}
    for v in videos:
        channel_counts[v["channel"]] = channel_counts.get(v["channel"], 0) + 1

    named_channels = {ch for ch, count in channel_counts.items() if count > CHANNEL_THRESHOLD}
    for v in videos:
        v["channel_display"] = v["channel"] if v["channel"] in named_channels else "Otros"

    # Audio overlap
    overlap = load_overlap()
    audio_meta = load_audio_meta()
    linked = 0
    for v in videos:
        audio_id = overlap.get(v["id"])
        if audio_id and audio_id in audio_meta:
            v["audio_id"] = audio_id
            linked += 1
        else:
            v["audio_id"] = None

    # Sort newest first
    videos.sort(key=lambda v: v["date"] or "0000", reverse=True)

    # Channel list for filter
    channels = sorted(named_channels)
    channels.append("Otros")

    output = {"channels": channels, "total": len(videos), "videos": videos}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"Written to {OUTPUT}")
    print(f"  Named channels: {len(named_channels)}")
    print(f"  Linked to audio: {linked}")
    for ch in channels:
        count = sum(1 for v in videos if v["channel_display"] == ch)
        print(f"    {ch}: {count}")


if __name__ == "__main__":
    main()
