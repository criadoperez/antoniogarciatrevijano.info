#!/usr/bin/env python3
"""
Apply speaker identification decisions to audio transcripts.

Reads decision files (speaker_decisions_XX.json) produced by LLM analysis,
renames originals to .sinidentificar.md/.srt, and writes new versions with
identified speaker names.

Usage:
    python apply_speaker_decisions.py                  # Dry run
    python apply_speaker_decisions.py --apply          # Apply changes
    python apply_speaker_decisions.py --apply --force  # Re-process already done
"""

import json
import re
import sys
from pathlib import Path

AUDIO_DIR = Path(__file__).parent / "ficheros" / "publicos" / "audios"
DECISIONS_FILE = Path(__file__).parent / "speaker_decisions.json"


def load_all_decisions() -> dict:
    """Load decisions from speaker_decisions.json into {episode_id: decision}."""
    all_decisions = {}
    with open(DECISIONS_FILE) as fh:
        decisions = json.load(fh)
    for d in decisions:
        if d.get("assignments"):
            all_decisions[d["id"]] = d
    return all_decisions


def apply_to_md(content: str, assignments: dict) -> str:
    """Replace LOCUTOR labels in markdown content."""
    result = content
    for locutor, name in assignments.items():
        # Replace **LOCUTOR_XX:** with **Name:**
        result = result.replace(f"**{locutor}:**", f"**{name}:**")
        # Replace in frontmatter speakers list
        result = result.replace(f'"{locutor}"', f'"{name}"')
    return result


def apply_to_srt(content: str, assignments: dict) -> str:
    """Replace LOCUTOR labels in SRT content."""
    result = content
    for locutor, name in assignments.items():
        result = result.replace(f"[{locutor}]", f"[{name}]")
    return result


def main():
    apply = "--apply" in sys.argv
    force = "--force" in sys.argv

    print("Loading decisions...")
    decisions = load_all_decisions()
    print(f"Loaded {len(decisions)} episode decisions with assignments.\n")

    if not decisions:
        print("No decisions found. Run the LLM analysis first.")
        return

    # Stats
    high = sum(1 for d in decisions.values() if d.get("confidence") == "high")
    medium = sum(1 for d in decisions.values() if d.get("confidence") == "medium")
    print(f"High confidence: {high}")
    print(f"Medium confidence: {medium}")
    print()

    applied = 0
    skipped = 0
    missing = 0

    for eid, decision in sorted(decisions.items()):
        md_path = AUDIO_DIR / f"{eid}.md"
        srt_path = AUDIO_DIR / f"{eid}.srt"
        backup_md = AUDIO_DIR / f"{eid}.sinidentificar.md"
        backup_srt = AUDIO_DIR / f"{eid}.sinidentificar.srt"

        if not md_path.exists() and not backup_md.exists():
            missing += 1
            continue

        if backup_md.exists() and not force:
            skipped += 1
            continue

        assignments = decision["assignments"]

        if not apply:
            # Dry run: just show what would happen
            if applied < 5:
                print(f"  {eid}: {assignments} [{decision.get('confidence', '?')}]")
            applied += 1
            continue

        # Read original content (from backup if exists, otherwise from current)
        source_md = backup_md if backup_md.exists() else md_path
        md_content = source_md.read_text(encoding="utf-8")

        # Rename original if not already backed up
        if not backup_md.exists():
            md_path.rename(backup_md)

        if srt_path.exists() and not backup_srt.exists():
            srt_path.rename(backup_srt)

        # Write new md
        new_md = apply_to_md(md_content, assignments)
        md_path.write_text(new_md, encoding="utf-8")

        # Write new srt
        if backup_srt.exists():
            srt_content = backup_srt.read_text(encoding="utf-8")
            new_srt = apply_to_srt(srt_content, assignments)
            srt_path.write_text(new_srt, encoding="utf-8")

        applied += 1

    print(f"\n{'Applied' if apply else 'Would apply'}: {applied}")
    if skipped:
        print(f"Skipped (already done): {skipped}")
    if missing:
        print(f"Missing .md files: {missing}")

    if not apply:
        print("\nDry run. Use --apply to rename originals and write identified versions.")


if __name__ == "__main__":
    main()
