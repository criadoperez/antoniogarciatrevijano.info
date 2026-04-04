#!/usr/bin/env python3
"""
identify_speakers.py — Replace LOCUTOR_XX labels with real names in audio transcripts.

Phase 1: Parse .info.json descriptions to extract named participants per episode.
Phase 2: Scan transcript text for name mentions that reveal which LOCUTOR is who.

Only assigns names when confidence is high. Unidentified speakers stay as LOCUTOR_XX.

Safety: never overwrites originals. Renames .md → .sinidentificar.md and
.srt → .sinidentificar.srt before writing new versions.

Usage:
    python identify_speakers.py                  # Dry run — show what would change
    python identify_speakers.py --apply          # Actually rename + write files
    python identify_speakers.py --apply --force  # Re-process already-processed files
"""

import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# ── Configuration ──────────────────────────────────────────────────────

AUDIO_DIR = Path(__file__).parent / "ficheros" / "publicos" / "audios"

# Canonical name for AGT — always use this, never abbreviations
AGT = "Antonio García-Trevijano"

# Patterns that identify AGT in descriptions (order matters: most specific first)
AGT_DESCRIPTION_PATTERNS = [
    r"García[- ]Trevijano",
    r"Trevijano",
]

# Patterns that identify AGT being addressed in transcript text.
# These appear in OTHER speakers' lines, addressing AGT.
AGT_ADDRESS_PATTERNS = [
    r"(?:Don |don )?Antonio(?:\s|,|\.|$)",       # "Don Antonio," / "Antonio,"
    r"(?:Don |don )?García[- ]?Trevijano",        # "Don García-Trevijano"
    r"Trevijano",                                  # "Trevijano"
    r"(?:el |al )maestro",                         # "el maestro" (common reference)
]

# Names to extract from descriptions. Maps regex → canonical name.
# Order: most specific first to avoid partial matches.
KNOWN_PERSONS = {
    r"Antonio\s+García[- ]Trevijano\s+Forte": AGT,
    r"Antonio\s+García[- ]Trevijano": AGT,
    r"Antonio\s+García\s+Paredes": "Antonio García Paredes",
    r"Roberto\s+Centeno": "Roberto Centeno",
    r"Adrián\s+Perales(?:\s+Pina)?": "Adrián Perales",
    r"Dalmacio\s+Negro": "Dalmacio Negro",
    r"Daniel\s+Sancho": "Daniel Sancho",
    r"Jorge\s+Sánchez\s+de\s+Castro": "Jorge Sánchez de Castro",
    r"José\s+Papí": "José Papí",
    r"Pedro\s+Gallego": "Pedro Gallego",
    r"Hilario\s+García": "Hilario García",
    r"Gabriel\s+Albiac": "Gabriel Albiac",
    r"Jesús\s+Murciego": "Jesús Murciego",
    r"Pedro\s+M(?:aría|\.)\s+González": "Pedro María González",
    r"Martín\s+Alonso": "Martín Alonso",
    r"José\s+María\s+Fernández\s+Isla": "José María Fernández Isla",
}

# Words that indicate technical/support roles (not main speakers)
TECH_ROLES = re.compile(
    r"colaboraci[oó]n\s+t[eé]cnica|presenciado|estudio|"
    r"t[eé]cnic[oa]|realizaci[oó]n",
    re.IGNORECASE,
)

# Minimum proportion of address-pattern hits for a LOCUTOR to be considered AGT
# e.g., if LOCUTOR_01 is addressed as "Antonio" in 3 out of 4 address events → 0.75
MIN_ADDRESS_CONFIDENCE = 0.6

# Minimum number of address events to trust Phase 2
MIN_ADDRESS_EVENTS = 2


# ── Data structures ────────────────────────────────────────────────────

@dataclass
class SpeakerAssignment:
    locutor: str           # e.g., "LOCUTOR_01"
    name: str              # e.g., "Antonio García-Trevijano"
    confidence: str        # "high" or "medium"
    reason: str            # explanation


@dataclass
class EpisodeData:
    episode_id: str
    md_path: Path
    srt_path: Path | None
    info_path: Path | None
    description: str = ""
    title: str = ""
    speakers: list = field(default_factory=list)    # from frontmatter
    md_content: str = ""
    srt_content: str = ""
    # Extracted info
    named_participants: list = field(default_factory=list)  # canonical names
    tech_crew: list = field(default_factory=list)            # tech/support names
    speaker_word_counts: dict = field(default_factory=dict)  # LOCUTOR_XX → word count
    assignments: list = field(default_factory=list)          # SpeakerAssignment list


# ── Parsing ────────────────────────────────────────────────────────────

def parse_frontmatter(content: str) -> dict:
    """Extract YAML frontmatter fields from .md content."""
    m = re.match(r"^---\n(.+?)\n---", content, re.DOTALL)
    if not m:
        return {}
    fm = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"')
    return fm


def extract_speakers_from_frontmatter(content: str) -> list[str]:
    """Extract speaker labels from frontmatter speakers field."""
    m = re.search(r'^speakers:\s*\[(.+)\]', content, re.MULTILINE)
    if not m:
        return []
    return [s.strip().strip('"') for s in m.group(1).split(",")]


def extract_participants_from_description(description: str) -> tuple[list[str], list[str]]:
    """
    Parse episode description to extract named participants and tech crew.
    Returns (participants, tech_crew).
    """
    if not description:
        return [], []

    participants = []
    tech_crew = []

    # Split description into main participants and tech section
    # Pattern: "Han intervenido X, Y y Z, con la colaboración técnica de A"
    tech_split = TECH_ROLES.split(description)
    main_text = tech_split[0] if tech_split else description
    tech_text = " ".join(tech_split[1:]) if len(tech_split) > 1 else ""

    # Extract known persons from main text
    for pattern, canonical in KNOWN_PERSONS.items():
        if re.search(pattern, main_text, re.IGNORECASE):
            if canonical not in participants:
                participants.append(canonical)

    # Extract known persons from tech text
    for pattern, canonical in KNOWN_PERSONS.items():
        if re.search(pattern, tech_text, re.IGNORECASE):
            if canonical not in tech_crew and canonical not in participants:
                tech_crew.append(canonical)

    return participants, tech_crew


def count_speaker_words(content: str) -> dict[str, int]:
    """Count words spoken by each LOCUTOR in the markdown transcript."""
    counts = {}
    for m in re.finditer(r"\*\*(\w+):\*\*\s*(.*)", content):
        spk = m.group(1)
        text = m.group(2)
        counts[spk] = counts.get(spk, 0) + len(text.split())
    return counts


def find_address_events(content: str) -> list[tuple[str, str]]:
    """
    Find events where a speaker addresses AGT by name.
    Returns list of (addressing_locutor, addressed_pattern).

    Logic: if LOCUTOR_02 says "fíjate, Antonio" → LOCUTOR_02 is addressing AGT.
    We then look at which LOCUTOR speaks nearby (before/after) to identify AGT.
    """
    events = []
    lines = content.split("\n")

    for i, line in enumerate(lines):
        # Check if this line has a speaker label and mentions AGT
        m = re.match(r"\*\*(\w+):\*\*\s*(.*)", line)
        if not m:
            continue
        speaker = m.group(1)
        text = m.group(2)

        for pattern in AGT_ADDRESS_PATTERNS:
            if re.search(pattern, text):
                events.append((speaker, pattern))
                break  # one match per line is enough

    return events


def identify_agt_from_addresses(
    address_events: list[tuple[str, str]],
    speaker_word_counts: dict[str, int],
    speakers: list[str],
) -> SpeakerAssignment | None:
    """
    Given that speaker X addressed AGT by name, AGT is likely a DIFFERENT speaker.
    Use word counts and context to determine which other speaker is AGT.

    Logic:
    - Collect all speakers who ADDRESS AGT → these are NOT AGT
    - Among remaining speakers, the one with the most words is likely AGT
    - AGT almost never speaks the least (he's the main voice)
    """
    if len(address_events) < MIN_ADDRESS_EVENTS:
        return None

    # Speakers who address AGT by name — they are NOT AGT
    addressers = set()
    for speaker, _ in address_events:
        addressers.add(speaker)

    # Main speakers (exclude DESCONOCIDO)
    main_speakers = [s for s in speakers if s not in ("DESCONOCIDO",)]

    # Candidates for AGT: main speakers who never address AGT
    candidates = [s for s in main_speakers if s not in addressers]

    if not candidates:
        return None

    # Among candidates, pick the one with most words (AGT talks the most)
    candidates_with_words = [
        (s, speaker_word_counts.get(s, 0)) for s in candidates
    ]
    candidates_with_words.sort(key=lambda x: x[1], reverse=True)
    best_candidate, best_words = candidates_with_words[0]

    # Confidence check: best candidate should have substantial word count
    total_words = sum(speaker_word_counts.values())
    if total_words == 0:
        return None

    word_share = best_words / total_words
    # AGT typically speaks 20%+ of the words
    if word_share < 0.15:
        return None

    return SpeakerAssignment(
        locutor=best_candidate,
        name=AGT,
        confidence="medium",
        reason=f"Phase 2: addressed as AGT {len(address_events)} times by {addressers}; "
               f"speaks {best_words} words ({word_share:.0%} of total)",
    )


def identify_agt_from_metadata(
    named_participants: list[str],
    speakers: list[str],
    speaker_word_counts: dict[str, int],
) -> SpeakerAssignment | None:
    """
    Phase 1: Use metadata (description) to identify AGT.

    High confidence when:
    - AGT is in the participant list
    - There are exactly 2 main speakers and 1 named participant (+ host)
      → the non-host speaker with more words is AGT

    Medium confidence when:
    - AGT is in the participant list
    - AGT is the only non-host named participant
    - The speaker with the most words (after the host) is likely AGT
    """
    if AGT not in named_participants:
        return None

    main_speakers = [s for s in speakers if s not in ("DESCONOCIDO",)]
    if len(main_speakers) < 2:
        return None

    # Sort speakers by word count descending
    ranked = sorted(
        [(s, speaker_word_counts.get(s, 0)) for s in main_speakers],
        key=lambda x: x[1],
        reverse=True,
    )
    total_words = sum(speaker_word_counts.values())
    if total_words == 0:
        return None

    other_participants = [p for p in named_participants if p != AGT]

    # HIGH confidence: only AGT as named participant (besides host/tech),
    # and exactly 2 main speakers → the one speaking more is AGT
    if len(other_participants) == 0 and len(main_speakers) == 2:
        # The speaker with more words is likely AGT (host introduces, AGT talks)
        best = ranked[0]
        return SpeakerAssignment(
            locutor=best[0],
            name=AGT,
            confidence="high",
            reason=f"Phase 1: only named participant, 2 speakers, "
                   f"speaks {best[1]} words ({best[1]/total_words:.0%})",
        )

    # HIGH confidence: 1 other named participant + AGT, exactly 3 main speakers
    # → host is lowest word count, AGT is highest (typically)
    if len(other_participants) == 1 and len(main_speakers) == 3:
        # AGT usually has the most words or second-most
        # The guest (other participant) typically has substantial words too
        # Host has the least
        # We can't be fully sure which of the top 2 is AGT vs guest
        # unless we can also identify the guest — skip for now, let Phase 2 handle
        pass

    # MEDIUM confidence: AGT is named, and one speaker has dominant word count
    if len(main_speakers) <= 4:
        best = ranked[0]
        word_share = best[1] / total_words
        # If one speaker has >40% of words and AGT is a participant, likely AGT
        if word_share > 0.40:
            return SpeakerAssignment(
                locutor=best[0],
                name=AGT,
                confidence="medium",
                reason=f"Phase 1: AGT named in description, dominant speaker with "
                       f"{best[1]} words ({word_share:.0%})",
            )

    return None


# ── File operations ────────────────────────────────────────────────────

def apply_assignments(content: str, assignments: list[SpeakerAssignment], fmt: str) -> str:
    """
    Replace LOCUTOR labels with real names in content.
    fmt: "md" or "srt"
    """
    result = content
    for a in assignments:
        if fmt == "md":
            # Replace **LOCUTOR_XX:** with **Name:**
            result = result.replace(f"**{a.locutor}:**", f"**{a.name}:**")
            # Replace in frontmatter speakers list
            result = result.replace(f'"{a.locutor}"', f'"{a.name}"')
        elif fmt == "srt":
            # Replace [LOCUTOR_XX] with [Name]
            result = result.replace(f"[{a.locutor}]", f"[{a.name}]")
    return result


# ── Main ───────────────────────────────────────────────────────────────

def load_episodes() -> list[EpisodeData]:
    """Load all episode data from the audio directory."""
    episodes = []
    seen_ids = set()

    for md_path in sorted(AUDIO_DIR.glob("*.md")):
        # Skip already-backed-up originals
        if ".sinidentificar" in md_path.name:
            continue

        episode_id = md_path.stem
        if episode_id in seen_ids:
            continue
        seen_ids.add(episode_id)

        srt_path = AUDIO_DIR / f"{episode_id}.srt"
        info_path = AUDIO_DIR / f"{episode_id}.info.json"

        ep = EpisodeData(
            episode_id=episode_id,
            md_path=md_path,
            srt_path=srt_path if srt_path.exists() else None,
            info_path=info_path if info_path.exists() else None,
        )

        # Load .md content
        ep.md_content = md_path.read_text(encoding="utf-8")
        ep.speakers = extract_speakers_from_frontmatter(ep.md_content)
        ep.speaker_word_counts = count_speaker_words(ep.md_content)

        # Load .srt content
        if ep.srt_path:
            ep.srt_content = ep.srt_path.read_text(encoding="utf-8")

        # Load .info.json metadata
        if ep.info_path:
            try:
                with open(ep.info_path, encoding="utf-8") as f:
                    info = json.load(f)
                ep.description = info.get("description", "")
                ep.title = info.get("title", "")
            except (json.JSONDecodeError, OSError):
                pass

        episodes.append(ep)

    return episodes


def process_episode(ep: EpisodeData) -> None:
    """Run Phase 1 and Phase 2 identification on an episode."""
    # Phase 1: Extract participants from description
    ep.named_participants, ep.tech_crew = extract_participants_from_description(
        ep.description
    )

    # Phase 1: Try to identify AGT from metadata
    assignment = identify_agt_from_metadata(
        ep.named_participants,
        ep.speakers,
        ep.speaker_word_counts,
    )

    # Phase 2: Try text-context identification if Phase 1 didn't find AGT
    if not assignment:
        address_events = find_address_events(ep.md_content)
        if address_events:
            assignment = identify_agt_from_addresses(
                address_events,
                ep.speaker_word_counts,
                ep.speakers,
            )

    # Phase 2 can also upgrade Phase 1 medium → high if both agree
    if assignment and assignment.confidence == "medium":
        address_events = find_address_events(ep.md_content)
        if address_events:
            phase2 = identify_agt_from_addresses(
                address_events,
                ep.speaker_word_counts,
                ep.speakers,
            )
            if phase2 and phase2.locutor == assignment.locutor:
                assignment.confidence = "high"
                assignment.reason += " + Phase 2 confirms"

    if assignment:
        ep.assignments.append(assignment)


def main():
    apply = "--apply" in sys.argv
    force = "--force" in sys.argv

    if not AUDIO_DIR.exists():
        print(f"ERROR: {AUDIO_DIR} not found.")
        sys.exit(1)

    print("Loading episodes...")
    episodes = load_episodes()
    print(f"Loaded {len(episodes)} episodes.\n")

    print("Processing...")
    for ep in episodes:
        process_episode(ep)

    # ── Statistics ──────────────────────────────────────────────────────

    total = len(episodes)
    identified = [ep for ep in episodes if ep.assignments]
    high = [ep for ep in identified if any(a.confidence == "high" for a in ep.assignments)]
    medium = [ep for ep in identified if any(a.confidence == "medium" for a in ep.assignments)
              and not any(a.confidence == "high" for a in ep.assignments)]
    unidentified = [ep for ep in episodes if not ep.assignments]
    has_description = [ep for ep in episodes if ep.description]
    agt_in_desc = [ep for ep in episodes if AGT in ep.named_participants]

    print(f"\n{'='*60}")
    print(f"IDENTIFICATION RESULTS")
    print(f"{'='*60}")
    print(f"Total episodes:              {total}")
    print(f"Have description:            {len(has_description)}")
    print(f"AGT named in description:    {len(agt_in_desc)}")
    print(f"")
    print(f"AGT identified (total):      {len(identified)} ({len(identified)/total:.1%})")
    print(f"  High confidence:           {len(high)}")
    print(f"  Medium confidence:         {len(medium)}")
    print(f"Not identified:              {len(unidentified)}")
    print()

    # Show some examples
    print("Sample identifications:")
    for ep in identified[:5]:
        for a in ep.assignments:
            print(f"  {ep.episode_id}: {a.locutor} → {a.name} [{a.confidence}]")
            print(f"    {a.reason}")
    print()

    if unidentified:
        print(f"Sample unidentified (first 5):")
        for ep in unidentified[:5]:
            desc_note = "no description" if not ep.description else (
                "AGT in desc" if AGT in ep.named_participants else "AGT not in desc"
            )
            print(f"  {ep.episode_id}: speakers={ep.speakers} ({desc_note})")
        print()

    if not apply:
        print("Dry run. Use --apply to rename originals and write identified versions.")
        return

    # ── Apply changes ──────────────────────────────────────────────────

    applied = 0
    skipped = 0

    for ep in identified:
        # Check if already processed
        backup_md = AUDIO_DIR / f"{ep.episode_id}.sinidentificar.md"
        backup_srt = AUDIO_DIR / f"{ep.episode_id}.sinidentificar.srt"

        if backup_md.exists() and not force:
            skipped += 1
            continue

        # Rename originals
        if not backup_md.exists():
            ep.md_path.rename(backup_md)
        if ep.srt_path and not backup_srt.exists():
            ep.srt_path.rename(backup_srt)

        # Read from backup (original content)
        md_original = backup_md.read_text(encoding="utf-8")
        new_md = apply_assignments(md_original, ep.assignments, "md")
        ep.md_path.write_text(new_md, encoding="utf-8")

        if ep.srt_path and backup_srt.exists():
            srt_original = backup_srt.read_text(encoding="utf-8")
            new_srt = apply_assignments(srt_original, ep.assignments, "srt")
            ep.srt_path.write_text(new_srt, encoding="utf-8")

        applied += 1

    print(f"Applied: {applied} episodes updated")
    if skipped:
        print(f"Skipped: {skipped} already processed (use --force to redo)")


if __name__ == "__main__":
    main()
