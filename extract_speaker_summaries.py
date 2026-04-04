#!/usr/bin/env python3
"""
Extract compact summaries from each audio episode for LLM-based speaker identification.
Writes a single JSON file with all episode summaries.

Prefers .sinidentificar.md (original transcripts with LOCUTOR labels) over .md
(which may already have speaker names replaced).
"""

import json
import re
from pathlib import Path

AUDIO_DIR = Path(__file__).parent / "ficheros" / "publicos" / "audios"
OUTPUT = Path(__file__).parent / "speaker_summaries.json"


def extract_speakers(content: str) -> list[str]:
    m = re.search(r'^speakers:\s*\[(.+)\]', content, re.MULTILINE)
    if not m:
        return []
    return [s.strip().strip('"') for s in m.group(1).split(",")]


def speaker_word_counts(content: str) -> dict[str, int]:
    counts = {}
    for m in re.finditer(r"\*\*(\w+):\*\*\s*(.*)", content):
        spk, text = m.group(1), m.group(2)
        counts[spk] = counts.get(spk, 0) + len(text.split())
    return counts


def first_lines_per_speaker(content: str, max_lines: int = 2, max_chars: int = 200) -> dict[str, list[str]]:
    """First N lines spoken by each speaker (truncated)."""
    result = {}
    for m in re.finditer(r"\*\*(\w+):\*\*\s*(.*)", content):
        spk, text = m.group(1), m.group(2)
        if spk not in result:
            result[spk] = []
        if len(result[spk]) < max_lines and text.strip():
            result[spk].append(text.strip()[:max_chars])
    return result


def lines_with_names(content: str) -> list[str]:
    """Lines where a speaker mentions a person's name (potential address events)."""
    patterns = [
        r"Don \w+", r"don \w+",
        r"Antonio", r"Trevijano", r"maestro",
        r"Roberto", r"Centeno", r"Dalmacio", r"Adrián",
        r"Daniel", r"Hilario", r"Gabriel", r"Pedro",
        r"Jorge", r"José", r"Fernando", r"Helena", r"Luis",
        r"Leopoldo", r"Miguel", r"Ángel", r"Lorenzo",
        r"Jesús", r"Martín", r"Agustín", r"David",
    ]
    combined = "|".join(patterns)
    hits = []
    for m in re.finditer(r"\*\*(\w+):\*\*\s*(.*)", content):
        spk, text = m.group(1), m.group(2)
        if re.search(combined, text):
            # Truncate but keep the relevant part
            hit = f"[{spk}] {text.strip()[:250]}"
            hits.append(hit)
            if len(hits) >= 12:
                break
    return hits


def main():
    # Collect episode files, preferring .sinidentificar.md (originals) over .md
    seen = set()
    md_files = []
    for md_path in sorted(AUDIO_DIR.glob("*.sinidentificar.md")):
        eid = md_path.name.replace(".sinidentificar.md", "")
        seen.add(eid)
        md_files.append((eid, md_path))
    for md_path in sorted(AUDIO_DIR.glob("*.md")):
        if ".sinidentificar" in md_path.name:
            continue
        eid = md_path.stem
        if eid not in seen:
            md_files.append((eid, md_path))

    episodes = []
    for eid, md_path in sorted(md_files, key=lambda x: x[0]):
        content = md_path.read_text(encoding="utf-8")

        # Load info.json
        info_path = AUDIO_DIR / f"{eid}.info.json"
        description = ""
        title = ""
        if info_path.exists():
            try:
                with open(info_path) as f:
                    info = json.load(f)
                description = info.get("description", "").strip()
                title = info.get("title", "").strip()
            except (json.JSONDecodeError, OSError):
                pass

        speakers = extract_speakers(content)
        words = speaker_word_counts(content)
        first = first_lines_per_speaker(content)
        names = lines_with_names(content)

        episodes.append({
            "id": eid,
            "title": title[:200] if title else "",
            "description": description[:500] if description else "",
            "speakers": speakers,
            "word_counts": words,
            "first_lines": first,
            "name_mentions": names,
        })

    OUTPUT.write_text(
        json.dumps(episodes, indent=1, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Extracted {len(episodes)} episode summaries to {OUTPUT}")
    # Print size
    size = OUTPUT.stat().st_size
    print(f"File size: {size / 1024:.0f} KB")


if __name__ == "__main__":
    main()
